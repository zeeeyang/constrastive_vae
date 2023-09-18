#CUDA_VISIBLE_DEVICES=2 python train_ptb.py --model mle_mi 1>mle_mi.cosonly.logexp.log 2>&1  
#CUDA_VISIBLE_DEVICES=2 python train_ptb.py --model mle 1>mle.regcos.klapprox.log 2>&1 & 

#CUDA_VISIBLE_DEVICES=1 python train_ptb_logexpall2.py --model mle_mi --results_folder_prefix "logexpall_aug" --num_epochs 60 1>mle_mi.logexpall.aug.log 2>&1 & 
#CUDA_VISIBLE_DEVICES=2 python train_ptb_logexpall3.py --model mle_mi --results_folder_prefix "logexpall_aug6" --num_epochs 40 1>mle_mi.logexpall.aug6.log 2>&1 & 
#CUDA_VISIBLE_DEVICES=2 python train_ptb_logexpall3.py --model mle_mi --results_folder_prefix "logexpall_aug6" --num_epochs 60 --train_from logexpall_aug6mle_mi/040.pt 1>mle_mi.logexpall.aug6.2.log 2>&1 & 
CUDA_VISIBLE_DEVICES=2 python train_ptb_logexpall3.py --model mle_mi --results_folder_prefix "logexpall_aug6" --num_epochs 60 1>mle_mi.logexpall.aug6.2.log 2>&1 & 
#CUDA_VISIBLE_DEVICES=2 python train_ptb_logexpall2.py --model mle --results_folder_prefix "logexpall_" --num_epochs 60 1>mle.logexpall.log 2>&1 & 

