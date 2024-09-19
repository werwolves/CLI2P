import os, torch, json, sys, pathlib

__dir__ = pathlib.Path(os.path.abspath(__file__))
sys.path.append(str(__dir__.parent))
sys.path.append(str(__dir__.parent.parent))
# print(f'sys-path:{sys.path}')

import numpy as np
import torch
import torch.backends.cudnn as cudnn
import torch.distributed as dist
import torch.nn as nn
import torch.optim as optim
from torch.optim.lr_scheduler import StepLR, ReduceLROnPlateau
from torch.utils.data import DataLoader
from data import load_dataset
from utils import get_lr
from tqdm import tqdm
from models.cli2p import CLI2P
from data import load_dataset
from dataloader import SiameseDataset, dataset_collate
from models import load_from_name, tokenize, image_transform
from tools import contrastive
from tools.utils import fit_one_epoch
from tools.callbacks import LossHistory

################################## ------------------------------------- 总体参数设置区域 begin ----------------------------------------- ########################################
# 是否使用cuda
use_cuda = True
# 模型保存的位置
save_dir = r'./model_weight_9_18'
# 数据地址
dataset_path = r'dataset'
# 图像编码器的输入尺寸
input_shape = [224, 224]
# batch大小
batch_size = 2
# 优化器的类型 ['adam' , 'sgd']
optimizer_type = 'adam'
# 学习率调整策略
lr_schedular_type = 'ReduceLROnPlateau'
# 是否使用多线程读取数据，0代表关闭多线程
num_workers = 0
# 是否使用DDP模式训练数据
distributed = False
# 是否使用预训练权重
pretrained   = True

################################## ------------------------------------- 总体参数设置区域 end ----------------------------------------- ########################################


ngpus_per_node  = torch.cuda.device_count()
if distributed:
    dist.init_process_group(backend="nccl")
    local_rank  = int(os.environ["LOCAL_RANK"])
    rank        = int(os.environ["RANK"])
    device      = torch.device("cuda", local_rank)
    if local_rank == 0:
        print(f"[{os.getpid()}] (rank = {rank}, local_rank = {local_rank}) training...")
        print("Gpu Device Count : ", ngpus_per_node)
else:
    device          = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    local_rank      = 0
    rank            = 0    




# step1: 数据加载模块

train_img_lines, train_text_lines, train_labels, val_img_lines, val_text_lines, val_labels = load_dataset(dataset_path)
# print(f"train_img_lines:{train_img_lines}")

## 训练数据
train_dataset  = SiameseDataset(input_shape, train_img_lines, train_text_lines, train_labels, True, autoaugment_flag=False)
train_dataloader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, collate_fn=dataset_collate, num_workers=num_workers, pin_memory=True)
## 训练数据
val_dataset  = SiameseDataset(input_shape, train_img_lines, train_text_lines, train_labels, True, autoaugment_flag=False)
val_dataloader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, collate_fn=dataset_collate, num_workers=num_workers, pin_memory=True)


# step2：模型加载模块
cli2p_model = CLI2P({}) 
if use_cuda:
    cli2p_model = cli2p_model.cuda()


model_path = os.path.join(save_dir, "best_epoch_weights.pth")
if pretrained and os.path.exists(model_path):
        cli2p_model_dict      = cli2p_model.state_dict()
        pretrained_dict = torch.load(model_path, map_location = device)
        load_key, no_load_key, temp_dict = [], [], {}
        for k, v in pretrained_dict.items():
            if k in cli2p_model_dict.keys() and np.shape(cli2p_model_dict[k]) == np.shape(v):
                temp_dict[k] = v
                load_key.append(k)
            else:
                no_load_key.append(k)
        cli2p_model_dict.update(temp_dict)
        cli2p_model.load_state_dict(cli2p_model_dict)
print(f'no_load_key:{no_load_key}')

# step3: 损失函数加载模块
loss_fn = contrastive.ContrastiveLoss()

# step4: 优化器的选择
Init_lr_fit = 3e-5
momentum      = 0.9
weight_decay  = 0
optimizer = {
    'adam'  : optim.Adam(cli2p_model.parameters(), Init_lr_fit, betas = (momentum, 0.999), weight_decay = weight_decay),
    'sgd'   : optim.SGD(cli2p_model.parameters(), Init_lr_fit, momentum=momentum, nesterov=True, weight_decay = weight_decay)
}[optimizer_type]


# step5: 
loss_history = LossHistory(save_dir, cli2p_model, input_shape=input_shape)



# step6：学习率调整单元


lr_schedular = {
    'StepLR': StepLR(optimizer=optimizer,step_size=6,gamma=0.9), # 每 2 轮 学习率 变成原来的 0.9倍
    'ReduceLROnPlateau': ReduceLROnPlateau(optimizer=optimizer, mode='min',factor=0.9, patience=3, eps=1e-10) # 当学习率小于eps之后，学习率将不在调整!!!
    
}[lr_schedular_type]




# step7: 开始训练
Epoch = 100
for epoch in range(Epoch):
    val_loss = fit_one_epoch(
        model=cli2p_model, 
        train_data_loader = train_dataloader,
        val_data_loader = val_dataloader,
        loss_fn = loss_fn,
        loss_history = loss_history,
        optimizer = optimizer,
        epoch_no=epoch,
        epoch_num =Epoch,
        per_epoch_train_steps= len(train_img_lines) // batch_size,
        per_epoch_val_steps = len(train_img_lines) // batch_size,
        save_weight_dir = save_dir,
        use_cuda = use_cuda,
    )
    lr_schedular.step(val_loss)


