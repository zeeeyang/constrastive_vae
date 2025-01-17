import sys
import os
from models_ptb import Decoder, Encoder, Nu_xz, Nu_z
from preprocess_ptb import Indexer
import torch
from torch import optim
from itertools import chain
import argparse
import logging
import torch.nn as nn
from data import Dataset
import numpy as np
import math
from tqdm import tqdm
import torch.nn.functional as F

parser = argparse.ArgumentParser()

# global parameters
parser.add_argument('--train_file', default='data/ptb-train.hdf5')
parser.add_argument('--val_file', default='data/ptb-val.hdf5')
parser.add_argument('--test_file', default='data/ptb-test.hdf5')
parser.add_argument('--results_folder_prefix', default='results_')
parser.add_argument('--train_from', default='')
parser.add_argument('--seed', default=0, type=int)
parser.add_argument('--test', action="store_true")
parser.add_argument('--log_prefix', default='eval')
parser.add_argument('--model', default='mle', type=str, choices=['mle', 'mle_mi'])

# KL cost annealing, increase beta from beta_0 by 1/warmup in certain steps
parser.add_argument('--warmup', default=10, type=int)
parser.add_argument('--beta_0', default=0.1, type=float)

# global training parameters
parser.add_argument('--num_epochs', default=40, type=int)
parser.add_argument('--num_particles_eval', default=128, type=int)

# use GPU
parser.add_argument('--gpu', default=0, type=int)
parser.add_argument('--no_gpu', action="store_true")

# model and optimizer parameters
parser.add_argument('--latent_dim', default=32, type=int)
parser.add_argument('--enc_word_dim', default=256, type=int)
parser.add_argument('--enc_h_dim', default=256, type=int)
parser.add_argument('--enc_num_layers', default=1, type=int)

parser.add_argument('--dec_word_dim', default=256, type=int)
parser.add_argument('--dec_h_dim', default=256, type=int)
parser.add_argument('--dec_num_layers', default=1, type=int)
parser.add_argument('--dec_dropout', default=0.5, type=float)
parser.add_argument('--enc_dropout', default=0.5, type=float)

parser.add_argument('--num_nu_updates', default=5, type=int)
parser.add_argument('--nu_lr', default=1e-5, type=float)
parser.add_argument('--end2end_lr', default=1e-3, type=float)
parser.add_argument('--max_grad_norm', default=5.0, type=float)

if sys.argv[1:] == ['0', '0']:
    args = parser.parse_args([])   # run in pycharm console
else:
    args = parser.parse_args()  # run in cmd

print("[tlog]", args)
# parameters
train_data = Dataset(args.train_file)
val_data = Dataset(args.val_file)
test_data = Dataset(args.test_file)
train_sents = train_data.batch_size.sum()
val_sents = val_data.batch_size.sum()
test_sents = test_data.batch_size.sum()
vocab_size = int(train_data.vocab_size)
vocab = Indexer()
vocab.load_vocab('data/ptb.dict')

print('Train data: %d batches' % len(train_data))
print('Val data: %d batches' % len(val_data))
print('Test data: %d batches' % len(test_data))
print('Train data: %d sentences' % train_sents)
print('Val data: %d sentences' % val_sents)
print('Test data: %d sentences' % test_sents)
print('Word vocab size: %d' % vocab_size)

results_folder = args.results_folder_prefix + args.model + '/'
if not os.path.exists(results_folder):
    os.makedirs(results_folder)

print("[tlog]", results_folder)

logging.basicConfig(filename=os.path.join(results_folder, args.log_prefix + '.log'),
                    level=logging.INFO, format='%(asctime)s--- %(message)s')
logging.info("the configuration:")
logging.info(str(args).replace(',', '\n'))
if not torch.cuda.is_available(): args.no_gpu = True
gpu = not args.no_gpu
if gpu: torch.cuda.set_device(args.gpu)

print(f"[tlog] seed: {args.seed}")
np.random.seed(args.seed)
prng = np.random.RandomState()
torch.manual_seed(args.seed)
if gpu: torch.cuda.manual_seed(args.seed)

beta = 1.0 if args.warmup == 0 else args.beta_0
epo_0 = 0
print("[tlog] === first 100 lines === ")

encoder = Encoder(vocab_size=vocab_size,
                  enc_word_dim=args.enc_word_dim,
                  enc_h_dim=args.enc_h_dim,
                  enc_num_layers=args.enc_num_layers,
                  latent_dim=args.latent_dim,
                  enc_dropout = args.enc_dropout) #tzy

decoder = Decoder(vocab_size=vocab_size,
                  dec_word_dim=args.dec_word_dim,
                  dec_h_dim=args.dec_h_dim,
                  dec_num_layers=args.dec_num_layers,
                  dec_dropout=args.dec_dropout,
                  latent_dim=args.latent_dim)

nu_xz = Nu_xz(enc_h_dim=args.enc_h_dim, latent_dim=args.latent_dim)
nu_z = Nu_z(latent_dim=args.latent_dim)
criterion = nn.NLLLoss()

nu_xz_optimizer = optim.Adam(nu_xz.parameters(), lr=args.nu_lr)
nu_z_optimizer = optim.Adam(nu_z.parameters(), lr=args.nu_lr)

end2end_optimizer = optim.Adam(chain(encoder.parameters(), decoder.parameters()), lr=args.end2end_lr)

print(f"[tlog args.train_from: {args.train_from}")

if args.train_from == "":
    for param in encoder.parameters():
        param.data.uniform_(-0.1, 0.1)
    for param in decoder.parameters():
        param.data.uniform_(-0.1, 0.1)
    if gpu:
        encoder = encoder.cuda()
        decoder = decoder.cuda()
        nu_xz = nu_xz.cuda()
        nu_z = nu_z.cuda()
        criterion.cuda()
else:
    logging.info('load model from' + args.train_from)
    checkpoint = torch.load(args.train_from, map_location="cuda:" + str(args.gpu) if gpu else 'cpu')

    if not args.test:  # if testing, start from random seed above; if continuing training, have a 'real' restart
        np_random_state = checkpoint['np_random_state']
        prng.set_state(np_random_state)
        torch_rng_state = checkpoint['torch_rng_state']
        torch_rng_state_cuda = checkpoint['torch_rng_state_cuda']
        torch.set_rng_state(torch_rng_state.cpu())
        if gpu: torch.cuda.set_rng_state(torch_rng_state_cuda.cpu())

    encoder = checkpoint['encoder']
    decoder = checkpoint['decoder']
    nu_xz = checkpoint['nu_xz']
    nu_z = checkpoint['nu_z']
    criterion = checkpoint['criterion']
    nu_xz_optimizer = checkpoint['nu_xz_optimizer']
    nu_z_optimizer = checkpoint['nu_z_optimizer']
    end2end_optimizer = checkpoint['end2end_optimizer']

    beta = checkpoint['beta']
    epo_0 = int(args.train_from[-6:-3])

logging.info("model configuration:")
logging.info(str(encoder))
logging.info(str(decoder))
logging.info(str(nu_xz))
logging.info(str(nu_z))

def kl_approx_loss(a, b): 
    return torch.mean(torch.exp(a) - b)

def kl_approx_loss_logexp(a,b): 
    '''
    zero = torch.zeros_like(a, device=a.device)
    new_a = torch.cat([zero, torch.exp(a)], dim=-1)
    new_b = torch.cat([zero, -b], dim=-1)
    return (torch.logsumexp(new_a, dim=-1) + torch.logsumexp(new_b, dim=-1)).mean()
    '''
    zero = torch.tensor([0.0], device=a.device)
    new_a = torch.cat([zero, torch.exp(a).view(-1)], dim=-1)
    new_b = torch.cat([zero, -b.view(-1)], dim=-1)
    return torch.logsumexp(new_a, dim=-1) + torch.logsumexp(new_b, dim=-1)

def nu_approx_loss_logexp(a): 
    #zero = torch.zeros_like(a, device=a.device)
    #new_a = torch.cat([zero, a], dim=-1)
    #return (torch.logsumexp(new_a, dim=-1)).mean()
    '''
    zero = torch.tensor([0.0], device=a.device)
    new_a = torch.cat([zero, a.view(-1)], dim=-1)
    return torch.logsumexp(new_a, dim=-1)
    '''
    return torch.mean(a)

def evaluation(data):
    encoder.eval()
    decoder.dec_linear.eval()
    decoder.dropout.eval()

    num_sents = 0.0
    num_words = 0.0
    total_rec = 0.0
    total_kl_xz = 0.0
    total_kl_z = 0.0
    total_mean_au = 0.0#torch.zeros(args.latent_dim, device='cuda' if gpu else 'cpu')
    total_sq_au = 0.0#torch.zeros(args.latent_dim, device='cuda' if gpu else 'cpu')

    pbar = tqdm(range(len(data)))
    for mini_batch in pbar:
        # logging.info('batch: %d' % mini_batch)
        #break

        sents_batch, length, batch_size = data[mini_batch]
        batch_size = batch_size.item()
        length = length.item()
        num_sents += batch_size
        num_words += batch_size * length
        if gpu: sents_batch = sents_batch.cuda()

        for bat in range(batch_size):
            idx = [bat] * args.num_particles_eval
            sents = sents_batch[idx, :]
            eps = torch.randn((args.num_particles_eval, args.latent_dim), device=sents_batch.device)
            z_x, enc, _ = encoder(sents, eps)
            z_x = z_x.data
            #print(z_x)

            # rec, kl
            preds = decoder(sents, z_x).data
            rec = sum([criterion(preds[:, l], sents[:, l + 1]) for l in range(preds.size(1))])
            total_rec += rec.item()
            z = torch.randn_like(eps)
            kl_xz = torch.mean(nu_xz(z_x, enc).data - torch.exp(nu_xz(z, enc)).data) + 1.0
            total_kl_xz += kl_xz.item()
            kl_z = torch.mean(nu_z(z_x).data - torch.exp(nu_z(z)).data) + 1.0
            total_kl_z += kl_z.item()

            # active units
            mean = torch.mean(z_x, dim=0)
            total_mean_au += mean.cpu().numpy()
            total_sq_au += (mean ** 2).cpu().numpy()

            del eps, z_x, z
            torch.cuda.empty_cache()

    rec = total_rec / num_sents
    kl_xz = total_kl_xz / num_sents
    nelbo = rec + kl_xz
    ppl = math.exp((total_rec + total_kl_xz) / num_words)
    kl_z = total_kl_z / num_sents
    mi = kl_xz - kl_z

    logging.info('rec: %.4f' % rec)
    logging.info('kl with nu: %.4f' % kl_xz)
    logging.info('neg_ELBO with nu: %.4f' % nelbo)
    logging.info('ppl: %.4f' % ppl)
    logging.info('kl_z: %.4f' % kl_z)
    logging.info('mi: %.4f' % mi)

    mean_au = total_mean_au / num_sents
    sq_au = total_sq_au / num_sents
    au_cov = sq_au - mean_au ** 2
    au = (au_cov >= 0.01).sum().item()
    logging.info('au_cov: %s' % str(au_cov))
    logging.info('au: %.4f' % au)

    report = "rec %f, kl_xz %f, elbo %f, \nppl %f, kl_z %f, mi %f, au %f\n" % (rec, kl_xz, nelbo, ppl, kl_z, mi, au)
    print(report)

    encoder.train()
    decoder.train()

    return nelbo


def sample_sentences(decoder, vocab, num_sentences, reconstruction=False, data=test_data):
    logging.info('---------------- Sample sentences: ----------------')
    decoder.eval()
    sampled_sents = []

    if reconstruction:
        sample_batch = torch.randint(len(data), (1,))
        sents_batch, length, batch_size = data[sample_batch]
        batch_size = batch_size.item()
        if gpu: sents_batch = sents_batch.cuda()
        eps = torch.randn((batch_size, args.latent_dim), device=sents_batch.device)
        z_x, _, _ = encoder(sents_batch, eps)
        expand_int = torch.randint(z_x.shape[0], (num_sentences,)).tolist()
        z_x = z_x.data[expand_int, :]
        sents = sents_batch.data[expand_int, :].tolist()
        sents = [[vocab.idx2word[s] for s in sents[i]] for i in range(num_sentences)]
    else:
        z_x = torch.randn((num_sentences, args.latent_dim), device='cuda' if gpu else 'cpu')

    for i in range(num_sentences):
        z = z_x[i, :]
        z = z.view(1, 1, -1)

        start = vocab.convert('<s>')
        START = torch.ones((), dtype=torch.long).new_tensor([[start]])
        end = vocab.convert('</s>')
        if gpu: START = START.cuda()
        sentence = sample_text(decoder, START, z, end)
        decoded_sentence = [vocab.idx2word[s] for s in sentence]
        sampled_sents.append(decoded_sentence)

    for i, sent in enumerate(sampled_sents):
        if reconstruction:
            logging.info(('the %d-th real sent: ') % i + ' '.join(sents[i]))
        logging.info(('the %d-th fake sent: ') % i + ' '.join(sent))


def sample_text(decoder, input, z, EOS):
    sentence = [input.item()]
    max_index = 0

    input_word = input
    batch_size, n_sample, _ = z.size()
    seq_len = 1
    z_ = z.expand(batch_size, seq_len, args.latent_dim)

    word_vecs = decoder.dec_word_vecs(input_word)
    decoder.h0 = torch.zeros((decoder.dec_num_layers, word_vecs.size(0), decoder.dec_h_dim), device=z.device)
    decoder.c0 = torch.zeros((decoder.dec_num_layers, word_vecs.size(0), decoder.dec_h_dim), device=z.device)
    decoder.h0[-1] = decoder.latent_hidden_linear(z)
    hidden = None

    while max_index != EOS and len(sentence) < 100:
        # (batch_size, seq_len, ni)
        word_embed = decoder.dec_word_vecs(input_word)
        word_embed = torch.cat((word_embed, z_), -1)

        if len(sentence) == 1:
            output, hidden = decoder.dec_rnn(word_embed, (decoder.h0, decoder.c0))
        else:
            output, hidden = decoder.dec_rnn(word_embed, hidden)

        preds = decoder.dec_linear[1:](output.view(word_vecs.size(0) * word_vecs.size(1), -1)).view(-1)
        max_index = torch.argmax(preds).item()
        input_word = torch.ones((), dtype=torch.long).new_tensor([[max_index]])
        if gpu: input_word = input_word.cuda()
        sentence.append(max_index)

    return sentence


def check_point(epo=None):
    check_pt = {
        'args': args,
        'encoder': encoder,
        'decoder': decoder,
        'nu_xz': nu_xz,
        'nu_z': nu_z,
        'criterion': criterion,
        'nu_xz_optimizer': nu_xz_optimizer,
        'nu_z_optimizer': nu_z_optimizer,
        'end2end_optimizer': end2end_optimizer,
        'beta': beta,
        'np_random_state': prng.get_state(),
        'torch_rng_state': torch.get_rng_state(),
        'torch_rng_state_cuda': torch.cuda.get_rng_state() if gpu else torch.get_rng_state()
    }
    if epo is not None:
        #torch.save(check_pt, os.path.join(results_folder, '%03d.pt' % epo))
        torch.save(check_pt, os.path.join(results_folder, 'checkpoint.pt'))
    else:
        torch.save(check_pt, os.path.join(results_folder, 'checkpoint.pt'))

print(f"[tlog] args.test: {args.test}")

if args.test:
    logging.info('\n------------------------------------------------------')
    logging.info("evaluation:")
    evaluation(test_data)
    # sample_sentences(decoder, vocab, num_sentences=50, reconstruction=False, data=test_data)
    # sample_sentences(decoder, vocab, num_sentences=50, reconstruction=True, data=test_data)
    exit()


logging.info('\n------------------------------------------------------')
logging.info("the current epo is %d of %d" % (epo_0, args.num_epochs))
print("the current epo is %d of %d" % (epo_0, args.num_epochs))
#sys.exit(0)
logging.info("evaluation:")
print("evaluation:")
check_point(epo_0)
#evaluation(test_data)
# sample_sentences(decoder, vocab, num_sentences=50, reconstruction=False, data=test_data)
# sample_sentences(decoder, vocab, num_sentences=50, reconstruction=True, data=test_data)
#sys.exit(0)

def compute_kl_loss(p, q):
    
    p_loss = F.kl_div(F.log_softmax(p, dim=-1), F.softmax(q, dim=-1), reduction='none')
    q_loss = F.kl_div(F.log_softmax(q, dim=-1), F.softmax(p, dim=-1), reduction='none')
    
    p_loss = p_loss.sum()
    q_loss = q_loss.sum()

    loss = (p_loss + q_loss) / 2
    return loss


class Similarity(nn.Module):
    """
        Dot product or cosine similarity
    """
    def __init__(self, temp):
        super().__init__()
        self.temp = temp
        self.cos = nn.CosineSimilarity(dim=-1)

    def forward(self, x, y):
        return self.cos(x, y) / self.temp


for epo in torch.arange(epo_0 + 1, args.num_epochs + 1):
    logging.info('\n------------------------------------------------------')
    logging.info("the current epo is %d of %d" % (epo, args.num_epochs))
    print("the current epo is %d of %d" % (epo, args.num_epochs))
    logging.info("training:")
    print("training:")

    # training
    encoder.train()
    decoder.train()

    sim_func = Similarity(0.05)
    self_sup_loss_fct = nn.CrossEntropyLoss()

    random_bat = torch.randperm(len(train_data)).tolist()
    pbar = tqdm(range(len(train_data)))
    for bat in pbar:
        mini_batch = random_bat[bat]
        sents, length, batch_size = train_data[mini_batch]
        batch_size = batch_size.item()
        length = length.item()
        if gpu: sents = sents.cuda()

        eps = torch.randn((batch_size, args.latent_dim), device=sents.device)
        eps2 = torch.randn((batch_size, args.latent_dim), device=sents.device)
        #tzy: check encoder use dropout or not??

        z_x, enc, enc_with_grad = encoder(sents, eps)
        z_x2, enc2, enc2_with_grad = encoder(sents, eps2)
        '''
        print(z_x.size(), z_x.requires_grad, z_x) #32, 256
        print(z_x2.size(), z_x2.requires_grad, z_x2)

        print(enc_with_grad.size(), enc_with_grad.requires_grad, enc_with_grad)
        print(enc2_with_grad.size(), enc2_with_grad.requires_grad, enc2_with_grad)
        sys.exit(0)
        ''' 
        beta = min(1, beta + 1. / (args.warmup * len(train_data)))

        # nu update
        for k in torch.arange(args.num_nu_updates):
            z_x_nu = z_x.data #no detach?
            z_x_nu2 = z_x2.data #no detach?
            #print("[tlog] z_x_nu: " + str(z_x_nu.requires_grad)) # False
            #print(z_x_nu) # 
            #print("[tlog] z_x: " + str(z_x.requires_grad)) # True
            #print(z_x) # 
            #print("[tlog] enc: " + str(enc.requires_grad)) # False
            #print(enc) # 
            #sys.exit(0)
            z = torch.randn_like(z_x_nu)
            z2 = torch.randn_like(z_x_nu2)
            '''
            if k >0: 
                z = torch.randn_like(z_x_nu)
                z2 = torch.randn_like(z_x_nu2)
            else:
                z = z_x.mean(dim=0, keepdim=True).expand_as(z_x_nu).data
                z2 = z_x2.mean(dim=0, keepdim=True).expand_as(z_x_nu2).data
            '''
            #nu_xz_loss = torch.mean(torch.exp(nu_xz(z, enc)) - nu_xz(z_x_nu, enc)) #no mi
            #nu_xz_loss += torch.mean(torch.exp(nu_xz(z2, enc2)) - nu_xz(z_x_nu2, enc2)) #no mi

            nu_xz_loss = kl_approx_loss_logexp(nu_xz(z, enc), nu_xz(z_x_nu, enc)) #no mi
            nu_xz_loss += kl_approx_loss_logexp(nu_xz(z2, enc2), nu_xz(z_x_nu2, enc2)) #no mi
            #nu_xz_loss *= 0.5
            
            nu_xz_optimizer.zero_grad()
            nu_xz_loss.backward()
            nu_xz_optimizer.step()
            #print(z_x_nu)
            #print(z_x_nu.requires_grad)
            #print(z_x_nu.grad)
            #sys.exit(0)
            del nu_xz_loss

            #nu_z_loss = torch.mean(torch.exp(nu_z(z)) - nu_z(z_x_nu))
            #nu_z_loss += torch.mean(torch.exp(nu_z(z2)) - nu_z(z_x_nu2))

            #nu_z_loss = kl_approx_loss_logexp(nu_z(z), nu_z(z_x_nu))
            #nu_z_loss += kl_approx_loss_logexp(nu_z(z2), nu_z(z_x_nu2))
            a, b  = nu_z(z), nu_z(z_x_nu)
            c, d = nu_z(z2), nu_z(z_x_nu2)
            sq_loss = ((a -c)**2).sum() + ((b - d) **2).sum()

            nu_z_loss = kl_approx_loss_logexp(a, b)
            nu_z_loss += kl_approx_loss_logexp(c, d)
            nu_z_loss += sq_loss

            #nu_z_loss *= 0.5

            nu_z_optimizer.zero_grad()
            nu_z_loss.backward()
            nu_z_optimizer.step()
            del nu_z_loss

        # end2end update
        preds = decoder(sents, z_x)
        preds2 = decoder(sents, z_x2)
        #tzy: todo add a r-drop here
        #print(preds.size(), preds2.size()) #32, 3, 10002 logits 
        #print(preds)
        #print(preds2)
        #decoder_kl_loss = compute_kl_loss(preds, preds2) * 0.1
        #print(decoder_kl_loss)
        #sys.exit(0)
        #tzy: need to check more
        rec = sum([criterion(preds[:, l], sents[:, l + 1]) for l in range(preds.size(1))])
        rec2 = sum([criterion(preds2[:, l], sents[:, l + 1]) for l in range(preds.size(1))])
        #rec = (rec + rec2)/2
        rec = (rec + rec2)

        if args.model == 'mle':
            #loss = rec + beta * torch.mean(nu_xz(z_x, enc))
            #loss = rec + beta * (torch.mean(nu_xz(z_x, enc)) + torch.mean(nu_xz(z_x2, enc2))) * 0.5
            loss = rec + beta * (nu_approx_loss_logexp(nu_xz(z_x, enc)) + nu_approx_loss_logexp(nu_xz(z_x2, enc2))) 
        else:
            #loss = rec + beta * torch.mean(nu_z(z_x))
            #loss = rec + beta * (torch.mean(nu_z(z_x)) + torch.mean(nu_z(z_x2))) * 0.5
            #loss = rec + beta * (torch.mean(nu_z(z_x)) + torch.mean(nu_z(z_x2)))
            a, b = nu_z(z_x), nu_z(z_x2)
            sq_loss = ((a-b)**2).sum()
            loss = rec + beta * (nu_approx_loss_logexp(a) + nu_approx_loss_logexp(b) + sq_loss)

        #loss += decoder_kl_loss

        #cos_sim = sim_func(enc_with_grad.unsqueeze(1), enc2_with_grad.unsqueeze(0))
        mean_z = z_x.mean(dim=0, keepdim=True)
        mean_z2 = z_x2.mean(dim=0, keepdim=True)
        ''' 
        max_z, _ = z_x.max(dim=0, keepdim=True)
        max_z2, _ = z_x2.max(dim=0, keepdim=True)
        min_z, _ = (-z_x).max(dim=0, keepdim=True)
        min_z2, _ = (-z_x2).max(dim=0, keepdim=True)
        min_z = -min_z
        min_z2 = -min_z2

        aug_zx = torch.cat([mean_z, max_z, min_z, z_x], dim=0)
        aug_zx2 = torch.cat([mean_z2, max_z2, min_z2, z_x2], dim=0)
        '''
        aug_zx = torch.cat([mean_z, z_x], dim=0)
        aug_zx2 = torch.cat([mean_z2, z_x2], dim=0)
        z_x, z_x2 = aug_zx, aug_zx2
        cos_sim = sim_func(z_x.unsqueeze(1), z_x2.unsqueeze(0))

        if args.model == 'mle':
            batch_size, feat_size = z_x.size()
            rand_vec = torch.normal(0, 1, size=(3*batch_size, feat_size)).to(z_x.device)
            reg_cos_sim = sim_func(z_x.unsqueeze(1), rand_vec.unsqueeze(0)) 
            cos_sim = torch.cat((cos_sim, reg_cos_sim), dim=1)

        #print(cos_sim.size())
        #print(cos_sim)
        labels = torch.arange(cos_sim.size(0)).long().to(enc_with_grad.device)
        self_sup_loss = self_sup_loss_fct(cos_sim, labels)
        #print(self_sup_loss)
        #sys.exit(0)
        loss += self_sup_loss

        end2end_optimizer.zero_grad()
        #print(z_x)
        #print(z_x.requires_grad)
        #print(z_x.grad)
        #sys.exit(0)
        loss.backward()
        if args.max_grad_norm > 0:
            torch.nn.utils.clip_grad_norm_(encoder.parameters(), args.max_grad_norm)
            torch.nn.utils.clip_grad_norm_(decoder.parameters(), args.max_grad_norm)
        end2end_optimizer.step()

        del loss
        torch.cuda.empty_cache()
        assert not torch.isnan(z_x).any(), 'training get nan z_x'

    # evaluation
    logging.info("evaluation:")
    print("evaluation:")
    check_point(epo)
    evaluation(test_data)
    # sample_sentences(decoder, vocab, num_sentences=50, reconstruction=False, data=test_data)
    # sample_sentences(decoder, vocab, num_sentences=50, reconstruction=True, data=test_data)
