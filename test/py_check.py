import sys, os
sys.path.insert(0, '../python') 

import ipdb
debug = 0

if debug:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    path = os.path.expanduser('~/coding/src/zsvision/python')
    sys.path.insert(0, path) # lazy
    from zsvision.zs_iterm import zs_dispFig

import torch
import argparse
import numpy as np
import scipy.io
import torchvision
from PIL import Image
from ast import literal_eval as make_tuple
import pathlib
from torch.autograd import Variable
import torchvision.transforms as transforms

if 1:
    sys.path.insert(0, os.path.expanduser('~/local/matlab-engine/lib'))
    sys.path.insert(0, 'python')

import pytorch_utils as pl

# compare against matconvnet
import matlab.engine
eng = matlab.engine.start_matlab()
cwd = pathlib.Path.cwd()

#ipdb.set_trace()
if 1:
    sys.argv = ['/Users/samuelalbanie/coding/libs/matconvnets/contrib-matconvnet/contrib/mcnPyTorch/test/py_check.py', '--image-size=[224,224]', '--is-torchvision-model=True', 'resnet18', 'models/resnet18-pt-mcn.mat']
    # sys.argv = ['/Users/samuelalbanie/coding/libs/matconvnets/contrib-matconvnet/contrib/mcnPyTorch/test/py_check.py', '--image-size=[224,224]', '--is-torchvision-model=True', 'squeezenet1_1', 'models/squeezenet1_1-pt-mcn.mat']


parser = argparse.ArgumentParser(
   description='Check activations of MatConvNet model imported from PyTorch.')
parser.add_argument('py_model',
                    type=str,
                    help='The input should be the name of a pytorch model \
                      (if present in pytorch.visionmodels), otherwise it \
                      should be a path to its .pth file')
parser.add_argument('mcn_model',
                    type=str,
                    help='Output MATLAB file')
parser.add_argument('--image-size',
                    type=str,
                    nargs='?',
                    default='[224,224]',
                    help='Size of the input image')
parser.add_argument('--remove-dropout', #TODO(sam): clean up, determine automatically
                    dest='remove_dropout',
                    action='store_true',
                    default=False,
                    help='Remove dropout layers') 
parser.add_argument('--is-torchvision-model',
                    type=bool,
                    nargs='?',
                    default=True,
                    help='is the model part of the torchvision.models')
args = parser.parse_args()

# params = torch.load(str(vgg))
if args.is_torchvision_model:
    net, flatten_loc = pl.load_valid_pytorch_model(args.py_model)
else:
    raise ValueError('not yet supported')

def get_inter_feats(net, x, feats=[]):
   if len(list(net.children())) == 0:
       return [net(x)]
   trunk = torch.nn.Sequential(*list(net.children())[:-1])
   feats = [*get_inter_feats(trunk, x, feats), net(x)]
   return feats

# generate image and convert to var
im_orig = Image.open(str(cwd / 'test/peppers.png')).convert('RGB')
image_size = tuple(make_tuple(args.image_size))
im = np.array(im_orig.resize(image_size))
normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                  std=[0.229, 0.224, 0.225])
transform = transforms.Compose([transforms.ToTensor(),normalize])
x = Variable(transform(im).unsqueeze(0))

# feature_feats = get_inter_feats(net.features.eval(), x)
# last = feature_feats[-1]
# last = last.view(last.size(0), -1)
# classifier_feats = get_inter_feats(net.classifier.eval(), last)
# py_feats_tensors = feature_feats + classifier_feats
py_feats_tensors = pl.compute_intermediate_feats(net.eval(), x, flatten_loc)

# create image to pass to MATLAB and compute the feature maps
im_np = np.array(torch.squeeze(x.data,0).numpy())
mcn_im = im_np.flatten().tolist() # no numpy support
eng.addpath(str(cwd/'test'),nargout=0)
mcn_feats_ = [np.array(x) for x in 
              eng.get_mcn_features(args.mcn_model, mcn_im, im_np.shape)]
py_feats = [np.squeeze(x.data.numpy()) for x in py_feats_tensors]
mcn_feats = [np.squeeze(np.transpose(x, (2,0,1))) for x in mcn_feats_] # to CxHxW
print('num mcn feature maps: {}'.format(len(mcn_feats)))
print('num py feature maps: {}'.format(len(py_feats)))

class PlaceHolder(object):

    def __init__(self, name, module_type):
        self.name = name
        self.module_type = module_type

    def __repr__(self):
        return '({}, {})'.format(self.module_type, self.name)

# determine feature pairing (accounts for the extra layers created to 
# match the flattening performed before the classifier in pytorch, as 
# well as the removal of dropout layers)
def module_execution_order(module):
    modules = []
    print('module type', type(module))
    children = list(module.children())
    print('num children: {}'.format(len(children)))
    if len(children) == 0:
        print('leaf node')
        modules.append(module)
    elif isinstance(module, torchvision.models.resnet.BasicBlock):
        print('block')
        assert len(children) == 5 + bool(module.downsample), 'unexpected children'
        submodules = children[:5]
        prefix = list(module.named_children())[0][0]
        if module.downsample:
            submodules.append(PlaceHolder('{}-proj'.format(prefix), 'proj'))
            submodules.append(PlaceHolder('{}-bn'.format(prefix), 'bn'))
        
        submodules.append(PlaceHolder('{}-merge'.format(prefix), 'sum'))
        submodules.append(PlaceHolder('{}-relu'.format(prefix), 'relu'))
        modules.extend(submodules)
        print('running')
    else:
        print('node')
        for child in children:
            print('proc: ', type(child))
            modules.extend(module_execution_order(child))
            print('stored', modules)
    return modules

def get_feature_pairs(net):
    feat_modules = module_execution_order(net.features)
    classifier_modules = module_execution_order(net.classifier)
    modules = feat_modules + classifier_modules
    py_feat_idx = list(range(len(modules) + 2))
    dropout_idx = [i + 1 for i,x in enumerate(modules)  # +1 for input im
            if isinstance(x, torch.nn.modules.dropout.Dropout)]
    pairs = [] 
    cursor = 0
    for py_idx in py_feat_idx:
        #if py_idx == len(feat_modules)+ 1:
        if py_idx == len(feat_modules):
            cursor += 1 # mcn flattening procedure uses an extra layer
        if py_idx in dropout_idx and args.remove_dropout:
            print('drop zone')
            continue
        if debug: print(py_idx, cursor)
        pairs.append([py_idx, cursor])
        cursor += 1
    return pairs

pairs = get_feature_pairs(net)

for py_idx, mcn_idx in pairs:
    py_feat = py_feats[py_idx]
    mcn_feat = mcn_feats[mcn_idx]
    print('{}v{}: size py: {} vs size mcn: {}'.format(py_idx,mcn_idx,
                      py_feat.shape, mcn_feat.shape))
    diff = np.absolute(py_feat - mcn_feat).mean()
    if diff > 1e-2: # allow a huge margin
        print('diff: {}'.format(diff))
        print('py mean: {}'.format(py_feat.mean()))
        print('mcn mean: {}'.format(mcn_feat.mean()))
        raise ValueError('numerical checks failed')

print('Success! the imported mcn-model is numerically equivalent to \
       its PyTorch counterpart')
