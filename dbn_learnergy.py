#General Imports
import os
import numpy as np
import random

import sys
sys.setrecursionlimit(4500)

#ML imports
import torch
import torch.nn as nn
import torch.optim as opt 
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import DataLoader
from torch.utils.data.distributed import DistributedSampler
#from torch.nn import DataParallel as DDP
from learnergy.models.deep import ResidualDBN, FCDBN

#Visualization
import learnergy.visual.convergence as converge
import matplotlib 
matplotlib.use("Agg")
import  matplotlib.pyplot as plt

#Data
from dbn_datasets import DBNDataset
from utils import numpy_to_torch, read_yaml, get_read_func, get_scaler

#Input Parsing
import yaml
import argparse
from datetime import timedelta

#Serialization
import pickle

def setup_ddp(rank, world_size, use_gpu=True, port=12355):
    os.environ['MASTER_ADDR'] = 'localhost'
    os.environ['MASTER_PORT'] = port
    os.environ['CUDA_VISIBLE_DEVICES'] = "0,1,2,3,4,5,6,7"
    os.environ['NCCL_SOCKET_IFNAME'] = "lo"

    driver = "nccl"
    if not use_gpu or not torch.cuda.is_available():
        driver = "gloo"

    # initialize the process group
    dist.init_process_group(driver, rank=rank, world_size=world_size, init_method="env://", timeout=timedelta(seconds=10))

def cleanup_ddp():
    dist.destroy_process_group()


def run_dbn(yml_conf, ddp_port):

    #Get config values 
    data_test = yml_conf["data"]["files_test"]
    data_train = yml_conf["data"]["files_train"]  	

    pixel_padding = yml_conf["data"]["pixel_padding"]
    number_channel = yml_conf["data"]["number_channels"]
    data_reader =  yml_conf["data"]["reader_type"]
    data_reader_kwargs = yml_conf["data"]["reader_kwargs"]
    fill = yml_conf["data"]["fill_value"]
    chan_dim = yml_conf["data"]["chan_dim"]
    valid_min = yml_conf["data"]["valid_min"]
    valid_max = yml_conf["data"]["valid_max"]
    delete_chans = yml_conf["data"]["delete_chans"]
    subset_count = yml_conf["data"]["subset_count"]
    output_subset_count = yml_conf["data"]["output_subset_count"]
    scale_data = yml_conf["data"]["scale_data"]

    transform_chans = yml_conf["data"]["transform_default"]["chans"]
    transform_values = 	yml_conf["data"]["transform_default"]["transform"]

    out_dir = yml_conf["output"]["out_dir"]
    os.makedirs(out_dir, exist_ok=True)

    model_fname = yml_conf["output"]["model"]
    training_output = yml_conf["output"]["training_output"]
    training_mse = yml_conf["output"]["training_mse"]
    testing_output = yml_conf["output"]["testing_output"]
    testing_mse = yml_conf["output"]["testing_mse"]

    model_type = yml_conf["dbn"]["params"]["model_type"]
    dbn_arch = tuple(yml_conf["dbn"]["params"]["dbn_arch"])
    gibbs_steps = tuple(yml_conf["dbn"]["params"]["gibbs_steps"])
    learning_rate = tuple(yml_conf["dbn"]["params"]["learning_rate"])
    momentum = tuple(yml_conf["dbn"]["params"]["momentum"])
    decay = tuple(yml_conf["dbn"]["params"]["decay"])
    normalize_learnergy = tuple(yml_conf["dbn"]["params"]["nesterov_accel"])
    batch_normalize = tuple(yml_conf["dbn"]["params"]["batch_normalize"])

    temp = None
    nesterov_accel = None

    visible_shape = None
    filter_shape = None
    stride = None
    n_filters = None
    if "conv" in model_type[0]:
        visible_shape = yml_conf["dbn"]["params"]["visible_shape"]
        filter_shape = yml_conf["dbn"]["params"]["filter_shape"]
        stride = yml_conf["dbn"]["params"]["stride"]
        n_filters = yml_conf["dbn"]["params"]["n_filters"]
    else:
        temp = tuple(yml_conf["dbn"]["params"]["temp"])
        nesterov_accel = tuple(yml_conf["dbn"]["params"]["nesterov_accel"])

    use_gpu = yml_conf["dbn"]["training"]["use_gpu"]
    world_size = yml_conf["dbn"]["training"]["world_size"]
    rank = yml_conf["dbn"]["training"]["rank"]
    device_ids = yml_conf["dbn"]["training"]["device_ids"]
    batch_size = yml_conf["dbn"]["training"]["batch_size"]
    epochs = yml_conf["dbn"]["training"]["epochs"]

    overwrite_model = yml_conf["dbn"]["overwrite_model"]

    scaler_type = yml_conf["scaler"]["name"]
    scaler, scaler_train = get_scaler(scaler_type)

 
    setup_ddp(rank, world_size, use_gpu, ddp_port)

    read_func = get_read_func(data_reader)

    #Generate model
    chunk_size = 1
    for i in range(1,pixel_padding+1):
        chunk_size = chunk_size + (8*i)

    #Generate training dataset object
    #Unsupervised, so targets are not used. Currently, I use this to store original image indices for each point 
    x2 = DBNDataset(data_train, read_func, data_reader_kwargs, pixel_padding, delete_chans=delete_chans, valid_min=valid_min, valid_max=valid_max, fill_value =fill, chan_dim = chan_dim, transform_chans=transform_chans, transform_values=transform_values, scalers = [scaler], train_scalers = scaler_train, scale = scale_data, transform=numpy_to_torch, subset=subset_count)
 
    fcn = False ##TODO fix

    model_file = os.path.join(out_dir, model_fname)
    print(not os.path.exists(model_file) or overwrite_model)
    if not os.path.exists(model_file) or overwrite_model:
        if not fcn:
            new_dbn = ResidualDBN(model=model_type, n_visible=chunk_size*number_channel, n_hidden=dbn_arch, steps=gibbs_steps, learning_rate=learning_rate, momentum=momentum, decay=decay, temperature=temp, use_gpu=use_gpu)
        else:
            new_dbn = FCDBN(model=model_type,visible_shape=(chunk_size, chunk_size, number_channel), n_channels=number_channel, steps=gibbs_steps, learning_rate=learning_rate, momentum=momentum, decay=decay, use_gpu=use_gpu)


        for i in range(len(new_dbn.models)):
            new_dbn.models[i].ddp_model = DDP(new_dbn.models[i], device_ids=device_ids).cuda()
            new_dbn.models[i]._optimizer = opt.SGD(new_dbn.models[i].ddp_model.parameters(), lr=learning_rate[i], momentum=momentum[i], weight_decay=decay[i], nesterov=nesterov_accel[i])
            new_dbn.models[i].normalize = normalize_learnergy[i]
            new_dbn.models[i].batch_normalize = batch_normalize[i]
 
        #Train model
        count = 0
        while(count == 0 or x2.has_next_subset()):
            mse, pl = new_dbn.fit(x2, batch_size=batch_size, epochs=epochs,
                is_distributed = True, num_loader_workers = int(os.cpu_count() / 3))
            count = count + 1
            x2.next_subset()            	
            converge.plot(new_dbn.models[i]._history['mse'],
                labels=['MSE'], title='convergence', subtitle='Model: Restricted Boltzmann Machine')
            plt.savefig(os.path.join(out_dir, "mse_plot_layer" + str(i) + ".png"))
            plt.clf()
            converge.plot( new_dbn.models[i]._history['pl'],
                labels=['log-PL'], title='Log-PL', subtitle='Model: Restricted Boltzmann Machine')
            plt.savefig(os.path.join(out_dir, "log-pl_plot_layer" + str(i) + ".png"))
            plt.clf()
            converge.plot( new_dbn.models[i]._history['time'],
                labels=['time (s)'], title='Training Time', subtitle='Model: Restricted Boltzmann Machine')
            plt.savefig(os.path.join(out_dir, "time_plot_layer" + str(i) + ".png"))
            plt.clf()

        #reset to first subset for output generation
        x2.next_subset()

    else:
        print("Loading pre-existing model")
        new_dbn = torch.load(model_file)   

    for i in range(len(new_dbn.models)):
        converge.plot(new_dbn.models[i]._history['mse'], new_dbn.models[i]._history['pl'],
            new_dbn.models[i]._history['time'], labels=['MSE', 'log-PL', 'time (s)'],
            title='convergence over MNIST dataset', subtitle='Model: Restricted Boltzmann Machine')
        plt.savefig(os.path.join(out_dir, "convergence_plot_layer" + str(i) + ".png"))

    if not os.path.exists(model_file) or overwrite_model:
        #Save model
        torch.save(new_dbn, os.path.join(out_dir, model_fname))


    #Generate test datasets
    x3 = None
    for t in range(0, len(data_test)):
        if t == 0:
            scaler = x2.scalers
            del x2
        else:
            scaler = x3.scalers
        x3 = DBNDataset([data_test[t]], read_func, data_reader_kwargs, pixel_padding, delete_chans=delete_chans, valid_min=valid_min, valid_max=valid_max, fill_value = fill, chan_dim = chan_dim, transform_chans=transform_chans, transform_values=transform_values, scalers=scaler, scale = scale_data, transform=numpy_to_torch, subset=subset_count)

        generate_output(x3, new_dbn, use_gpu, out_dir, "file" + str(t) + "_" +  testing_output, testing_mse, output_subset_count)
    
    #Generate test datasets. Doing it this way, because generating all at once creates memory issues
    x2 = None
    for t in range(0, len(data_train)):
        if t == 0:
            scaler = x3.scalers
            del x3
        else:
            scaler = x2.scalers
        x2 = DBNDataset([data_train[t]], read_func, data_reader_kwargs, pixel_padding, delete_chans=delete_chans, valid_min=valid_min, valid_max=valid_max, fill_value =fill, chan_dim = chan_dim, transform_chans=transform_chans, transform_values=transform_values, scalers = scaler, scale = scale_data, transform=numpy_to_torch, subset=subset_count)

        generate_output(x2, new_dbn, use_gpu, out_dir, "file" + str(t) + "_" +  training_output, training_mse, output_subset_count)

    cleanup_ddp()

def generate_output(dat, mdl, use_gpu, out_dir, output_fle, mse_fle, output_subset_count):
    output_full = None
    rec_mse_full = []
    count = 0
    dat.subset = output_subset_count
    dat.current_subset = -1
    dat.next_subset()
    while(count == 0 or dat.has_next_subset() or dat.current_subset > (dat.subset-2)):
        if torch.cuda.is_available() and use_gpu:
            output = mdl.models[0].ddp_model(dat.data.cuda(non_blocking=True)) #.cpu()
            for i in range(1, len(mdl.models)):
                output = mdl.models[i].ddp_model(output)
            output = output.cpu()
            dat.transform = None

            sampler = DistributedSampler(dat)
            loader = DataLoader(dat, batch_size=len(dat), shuffle=False,
                    sampler=sampler, num_workers = int(os.cpu_count() / 3), pin_memory = True,
                    drop_last=True) 
            rec_mse, v = mdl.reconstruct(dat, loader)
        else:
            output = mdl.forward(dat.data)
            dat.transform = None
            loader = DataLoader(
                dataset, batch_size=batch_size, shuffle=False, num_workers=int(os.cpu_count() / 3)
            )
            rec_mse, v = mdl.reconstruct(dat)
     
        rec_mse_full.append(torch.unsqueeze(rec_mse, 0)) 
        if output_full is None:
            output_full = output
        else:
            output_full = torch.cat((output_full,output))
        print("CONSTRUCTING OUTPUT", dat.data.shape, dat.data_full.shape, output.shape, output_full.shape)
        del output
        del rec_mse
        count = count + 1
        if dat.has_next_subset():
            dat.next_subset()
        else:
            break 

    #Save training output
    torch.save(output_full, os.path.join(out_dir, output_fle), pickle_protocol=pickle.HIGHEST_PROTOCOL)
    torch.save(dat.targets_full, os.path.join(out_dir, output_fle + ".indices"), pickle_protocol=pickle.HIGHEST_PROTOCOL)
    torch.save(dat.data_full, os.path.join(out_dir, output_fle + ".input"), pickle_protocol=pickle.HIGHEST_PROTOCOL)
    torch.save(torch.cat(rec_mse_full, dim=0), os.path.join(out_dir, mse_fle), pickle_protocol=pickle.HIGHEST_PROTOCOL)





def main(yml_fpath, ddp_port):
   
    #Translate config to dictionary 
    yml_conf = read_yaml(yml_fpath)
    #Run 
    run_dbn(yml_conf, ddp_port)


if __name__ == '__main__':
	
    parser = argparse.ArgumentParser()
    parser.add_argument("-y", "--yaml", help="YAML file for DBN and output config.")
    parser.add_argument("-p", "--port", required=False, default='12355')
    args = parser.parse_args()
    main(args.yaml, args.port)



