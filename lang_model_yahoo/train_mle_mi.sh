CUDA_VISIBLE_DEVICES=1 python train_yahoo.py --model mle_mi --num_epochs 60 1>mlemi.d0.5.1.log 2>&1 &
CUDA_VISIBLE_DEVICES=2 python train_yahoo.py --model mle --num_epochs 60 1>mle.d0.5.2.log 2>&1 &
