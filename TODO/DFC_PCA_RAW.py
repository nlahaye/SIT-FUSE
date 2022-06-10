import matplotlib
matplotlib.use('agg')
import matplotlib.pyplot as plt
 
from RAW_RAW_FUSE_DATA_DFC_PCA_2017 import TRAIN_DATA_OUTPUT, TEST_DATA_OUTPUT
from sklearn import metrics, manifold
from pprint import pprint
import numpy as np

from sklearn.decomposition import PCA

import os
import logging
import argparse

import scipy

import sys
from matplotlib.colors import ListedColormap
from CMAP import CMAP, CMAP_COLORS
sys.setrecursionlimit(4500)

#PERPLEXITY = [750, 1000, 1500]
PERPLEXITY = [10000] 

#PERPLEXITY = [5, 10, 20, 50, 2500, 500, 100]
N_ITER = 5000
STOP_NO_PROGRESS = 1000
N_JOBS = 50
LEARNING_RATE = 200

logging.basicConfig()
LOGGER = logging.getLogger("lrn2")
LOGGER.setLevel(logging.DEBUG)

from sklearn.preprocessing import StandardScaler
scalers = [None]*18
TRAIN = True
LANDSAT_FILL = -99999

import openTSNE

def trainScalers(filenames, chunk_size=5):

    data = []
    for i in range(0, len(filenames)):
        if os.path.exists(filenames[i]):
            dat = np.load(filenames[i])
            print(dat.shape)
            if dat.shape[0] > 0:
                data.append(dat)

            print(dat.min(), dat.max(), dat.mean(), dat.std())



    global scalers
    for r in range(len(data)):
        for n in range(data[r].shape[0]):
            if scalers[n] is None:
                scalers[n] = StandardScaler()
            subd = data[r][n,:,:]
            scalers[n].partial_fit(subd[np.where(subd > LANDSAT_FILL)].reshape(-1, 1))



def test_generic():

	fns = [] #fnames
	td = [[]]
	
	td = [TEST_DATA_OUTPUT]
	td.append(TRAIN_DATA_OUTPUT)
	trn = [TRAIN_DATA_OUTPUT]

	for l in range(len(td)):
		fns.append([])
		for i in range(len(td[l]["Output"])):
			fns[l].append(td[l]["Output"][i]) 
			



	trainScalers(trn[0]["Output"])

	trainData = None
	global scalers
	for i in range(len(trn[0]["Output"])):
		tmp, coord = format_data(trn[0]["Output"][i], trn[0]["Clust"][i])
		print(tmp.shape, coord.shape, "HERE")
		#for n in range(tmp.shape[0]):
		#	subd = tmp[n,:,:]
		#	subd = scalers[n].transform(subd.reshape(-1,1)).reshape(-1)
		#	tmp[n,:,:] = subd.reshape((tmp.shape[1], tmp.shape[2]))
		#tmp = tmp.swapaxes(0,2)
		#print(tmp.shape, "HERE")
		#if i == 0:
		#	trainData = tmp.reshape((tmp.shape[0]*tmp.shape[1], tmp.shape[2]))
		#else:
		#	trainData = np.concatenate((trainData,tmp.reshape((tmp.shape[0]*tmp.shape[1], tmp.shape[2]))))
		if i == 0:
			trainData = tmp
		else:
			print(trainData.shape, tmp.shape, "CONCAT")
			trainData = np.concatenate((trainData,tmp))
		print(trainData.shape, "TD SHAPE")

	print(trainData.shape)
	pca = PCA(n_components=0.99)
	X_pca = pca.fit(trainData)
 
	del trainData
	del tmp
	#del subd	
	for l in range(len(td)-1, -1, -1):
		for j in range(len(td[l]["Output"])):	
			test_data, coord = format_data(td[l]["Output"][j], td[0]["Clust"][j])
			print(test_data.shape)
			clust_data = np.load(td[l]["Clust"][j])
			test_chunks = [3]

			sys.stdout.flush()

			print(test_data.shape)
			print(test_data.shape)

			print(test_data.shape, clust_data.shape)
	
			tsne_data = pca.transform(test_data)
			final = plot_tsne(tsne_data, clust_data, coord)
			if final is None:
				print("BAD TSNE, MOVING ON")
				continue

			fname = td[l]["Clust"][j] + ".PCA99" + ".LR500.npy"
			np.save(fname, tsne_data)
			cmap = ListedColormap(CMAP_COLORS[0:500])
			img = plt.scatter(tsne_data[:, 0], tsne_data[:, 1], c=final.astype(np.int32), alpha=0.2)
			img.set_cmap(cmap)
			plt.savefig(fname + ".png", dpi=400)
			plt.clf()
				                


def plot_tsne(tsne_data, clust_data, coord):


	fnl = max(tsne_data[:,0])
	fnl2 = max(tsne_data[:,1])
	strt = 0
	strt2 = 0

	#if tsne_data[:,0].max() < 1 or tsne_data[:,1].max() < 1:
	#	return None

	#data = np.zeros(((int)(fnl-strt)+1, (int)(fnl2-strt2)+1)) - 1

	#print(tsne_data.min(), tsne_data.max(), tsne_data.mean(), tsne_data.std())

	clust_flat = np.zeros((coord.shape[0], 1))
	for pt in range(coord.shape[0]):
		clust_flat[pt] = clust_data[coord[pt,1], coord[pt,2]]
	#	print(data.shape, tsne_data.shape, clust_data.shape)
	#	print(tsne_data[pt,0], tsne_data[pt,1])
	#	print(coord[pt,1], coord[pt,2])	
	#	data[tsne_data[pt,0], tsne_data[pt,1]] = clust_data[coord[pt,1], coord[pt,2]]
	
	#clust_flat = np.array(clust_flat)
	#cmap = ListedColormap(CMAP_COLORS[0:500])
	#plt.scatter(tsne_data[:, 0], tsne_data[:, 1], c=clust_flat.astype(np.int32), alpha=0.2)	
	 

	return clust_flat



def format_data(fname, clust):

    dataRet = None
    if os.path.exists(fname):

        dat = np.load(fname)
        clust_data = np.load(clust)

        global scalers
        for n in range(dat.shape[0]):
            subd = dat[n,:,:]
            subd[np.where(subd > LANDSAT_FILL)] = scalers[n].transform(subd[np.where(subd > LANDSAT_FILL)].reshape(-1, 1)).reshape(-1)
            print("SCALED STATS", subd[np.where(subd > LANDSAT_FILL)].min(), subd[np.where(subd > LANDSAT_FILL)].max(), subd[np.where(subd > LANDSAT_FILL)].mean(), subd[np.where(subd > LANDSAT_FILL)].std())
            dat[n,:,:] = subd

        clust_data = clust_data[1:clust_data.shape[0]-1, 1:clust_data.shape[1]-1]
        dat = dat[:,:clust_data.shape[0], :clust_data.shape[1]]
	 

        contCount = 0

        dataRet = []
        dataCoord = []
        for j in range(1, dat.shape[1]-1):
            for k in range(1, dat.shape[2]-1):
                sub_data_total = []
                for c in range(0, dat.shape[0]):
                    sub_data = dat[c, j-1:j+2, k-1:k+2]
                    sub_data2 = sub_data.reshape(9)
                    sub_data_total.append(sub_data2)

                sub_data_total = np.array(sub_data_total)
                #if(sub_data_total[36:].min() <= LANDSAT_FILL):
                if(sub_data_total.min() <= LANDSAT_FILL):
                    contCount = contCount + 1
                    continue

                if(sub_data_total.shape[1] != 9):
                    print("ERROR:", sub_data_total.shape)

                #dataRet.append(sub_data_total[:36].ravel())
                dataRet.append(sub_data_total.ravel())
                dataCoord.append([0,j,k])

    dataRet = np.array(dataRet).astype(np.float32)
    dataCoord = np.array(dataCoord).astype(np.int8)
    print(dataRet.shape)
    return dataRet, dataCoord



def main():
	global scalers
	parser = argparse.ArgumentParser(description = "Run a complete lrn2 work flow")

	test_generic()
	




if __name__ == '__main__':
    main()


	



