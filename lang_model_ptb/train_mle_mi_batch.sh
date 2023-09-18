dropout=0.3
batch=64
CUDA_VISIBLE_DEVICES=1 python train_ptb_logexpall3_b${batch}.py --model mle_mi --results_folder_prefix "logexpall_batch${batch}d${dropout}" --num_epochs 60 --enc_dropout ${dropout} 1>mle_mi.logexpall.batch${batch}.d${dropout}.log 2>&1 &
