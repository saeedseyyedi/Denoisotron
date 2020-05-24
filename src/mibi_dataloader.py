import torch
import os
import gc
from skimage import io
import numpy as np
import random
from random import randint
from random import choice
import math
import copy
import pickle
from scipy.ndimage import gaussian_filter
import exifread
from skimage import io as skimage_io
from scipy import io as scipy_io
from PIL import Image, ImageSequence
from PIL.TiffTags import TAGS
import csv


def get_labels_from_csv(csvpath):
    label_dict = dict()
    with open(csvpath) as csvfile:
        csvreader = csv.reader(csvfile, delimiter=',')
        linenum = 0
        for row in csvreader:
            linenum += 1
            if linenum != 1:
                point = row[0]
                label = row[1]
                if label.isnumeric():
                    label = float(label)
                label_dict[point] = label
    return label_dict


def get_tensor_from_dict(point, field, **kwargs):
    """
    :param point: a dictionary expected to contain a 'markers' key, corresponding to 'labels' in MATLAB-verse. Note,
    should be either a list or an np-array object array of strings
    :param field: the field you wish to extract extract from the point as a matrix, such as 'counts'
    :param kwargs: you may optionally specify which markers you want to extract with the 'markers' key-word argument,
    specify as an iterable of strings.
    :return: a 32-bit floating point tensor that is M x W x H, where M is the number of markers, W and H
    are width and height of the variable specified by 'field'
    """
    markers = point['markers']
    test_img = torch.tensor(point[field][markers[0]])
    width = test_img.size(0)
    height = test_img.size(1)

    if 'markers' in kwargs:
        tensor = torch.zeros([len(kwargs['markers']), width, height]).float()
        for i in range(len(kwargs['markers'])):
            marker = kwargs['markers'][i]
            tensor[i, :, :] = torch.tensor(point[field][marker]).float()
    else:
        tensor = torch.zeros([len(markers), width, height]).float()
        for i in range(len(markers)):
            marker = markers[i]
            tensor[i, :, :] = torch.tensor(point[field][marker]).float()
    return tensor, markers


def loadmat(filename):
    '''
    RIPPED off of stackoverflow
    this function should be called instead of direct spio.loadmat
    as it cures the problem of not properly recovering python dictionaries
    from mat files. It calls the function check keys to cure all entries
    which are still mat-objects
    '''
    data = scipy_io.loadmat(filename, struct_as_record=False, squeeze_me=True)
    return _check_keys(data)


def _check_keys(dict):
    '''
    RIPPED off of stackoverflow
    checks if entries in dictionary are mat-objects. If yes
    todict is called to change them to nested dictionaries
    '''
    for key in dict:
        if isinstance(dict[key], scipy_io.matlab.mio5_params.mat_struct):
            dict[key] = _todict(dict[key])
    return dict


def _todict(matobj):
    '''
    RIPPED off of stackoverflow
    A recursive function which constructs from matobjects nested dictionaries
    '''
    dict = {}
    for strg in matobj._fieldnames:
        elem = matobj.__dict__[strg]
        if isinstance(elem, scipy_io.matlab.mio5_params.mat_struct):
            dict[strg] = _todict(elem)
        else:
            dict[strg] = elem
    return dict


def loadpoint_mat_file(point_path):
    point = loadmat(point_path)
    markers = point['markers']
    for i in range(len(markers)):
        markers[i] = markers[i].strip()
    point['markers'] = markers.tolist()
    return point


def savepoint_mat(point, point_path):
    point['markers'] = np.array(point['markers'], dtype=np.object)
    scipy_io.savemat(point_path + '.mat', point)


# Used to load a Point organized as a folder of tif images
def loadpoint_tiff_folder(point_path, subfolder='TIFs'):
    contents = listdir(os.path.join(point_path, subfolder))
    tifs = [i for i in contents if i.endswith('.tif') or i.endswith('.tiff')]
    tifs.sort()

    markers = list()
    counts = dict()
    tags = dict()
    for tif in tifs:
        marker = tif.split('.')[0]
        markers.append(marker)
        counts[marker] = skimage_io.imread(os.path.join(point_path, subfolder, tif)).astype(int)

        tif = Image.open(os.path.join(point_path, subfolder, tif))
        subtags = dict()
        for key in tif.tag:
            tagname = TAGS.get(key, 'missing')
            subtags[tagname] = tif.tag[key][0]
        tags[marker] = subtags
    point = dict()
    point['counts'] = counts
    point['markers'] = markers
    point['tags'] = tags
    point['path'] = point_path
    return point


# Used to load a Point organized as a multi-page tif
def loadpoint_tiff_multi(point_path):
    multitiff = Image.open(point_path)

    markers = list()
    counts = dict()
    tags = dict()

    for i in range(multitiff.n_frames):
        multitiff.seek(i)
        subtags = dict()
        for key in multitiff.tag:
            tagname = TAGS.get(key, 'missing')
            subtags[tagname] = multitiff.tag[key]
            if tagname == 'PageName':
                marker = multitiff.tag[key][0]
        markers.append(marker)
        counts[marker] = np.array(multitiff).astype(int)
        tags[marker] = subtags

    point = dict()
    point['counts'] = counts
    point['markers'] = markers
    point['tags'] = tags
    point['path'] = point_path

    return point


def loadpoint_tiff_single(tif_path):
    tif = Image.open(tif_path)

    markers = list()
    counts = dict()
    tags = dict()

    marker = 'unknown'
    subtags = dict()
    for key in tif.tag:
        tagname = TAGS.get(key, 'missing')
        subtags[tagname] = tif.tag[key]
        if tagname == 'PageName':
            marker = tif.tag[key][0]
    if marker == 'unknown':
        marker = os.path.basename(tif_path).split('.')[0].split('_')[-1]
    markers.append(marker)
    counts[marker] = np.array(tif).astype(int)
    tags[marker] = subtags

    point = dict()
    point['counts'] = counts
    point['markers'] = markers
    point['tags'] = tags
    point['path'] = tif_path

    return point


def listdir(folder):
    contents = os.listdir(folder)
    contents = [i for i in contents if not i.startswith('.')]
    return contents


def get_content(folder):
    return os.path.join(folder, listdir(folder)[0])


def read_tif_as_tensor(path):
    img = torch.tensor(skimage_io.imread(path))
    if img.dim() == 2:
        return img.unsqueeze(0).unsqueeze(0)
    elif img.dim() == 3:
        return img.unsqueeze(0)
    else:
        raise SystemExit('weird tif')


class PointReader:
    """
    Used to load the raw data of a MIBI dataset, the main function is "load_data", which takes in 4 main arguments:
    data_folder specifies where the data is
    label_type specifies whether and how the data is labeled
    input_format specifies the format of the raw data to be read
    output_format specifies the desired organization of the data for the MIBIDataLoader
    kwargs are option arguments, more details in the load_data docstring
    """
    # data_folder is the folder full of data
    # input_format is one of ['tiff', 'multitiff', 'tiffolder', 'matfile']
    # output_format is one of ['point', 'marker', 'pixel']
    # label_args:
    #   label_type is one of ['regression', 'categorical']
    #   label_format is one of ['image', 'folder', 'csv']

    @staticmethod
    def load_data(data_folder, input_format, output_format, label_args, **kwargs):
        """
        :param data_folder: directory where all data for the dataset is stored
        :param label_type: label_type should be one of ['none', 'pixel', 'point']. If 'none', assumes that data is just
        strewn about about inside of the data_folder. If 'pixel', assumes that each point is nested inside of a folder
        that also contains a label image containing pixel-wise labels. If 'point' assumes that the data_folder contains
        label folders, named by their label, inside of each being all the points with that label
        :param input_format: input_format should be one of ['tiff', 'multitiff', 'tifffolder', 'matfile']. If 'tiff',
        points are assumed to be single-image tiff files (presumably channels have been seperated). If 'multitiff'
        points are expected to be multi-page tiffs, with each page corresponding to a channel. If 'tifffolder', points
        are expected to be organized as Point folders containing some substructure (usually a TIFs folder) containting
        single-page TIF files corresponding to individual channels. If 'matfile', points are expected to be organized as

        :param output_format:
        :param kwargs:
        :return:
        """
        results = PointReader.get_metadata(data_folder, label_args, **kwargs)
        results = PointReader.get_raw_data(results, input_format, **kwargs)
        results = PointReader.reorganize_data(results, output_format, label_args, **kwargs)
        results = PointReader.organize_labels(results, label_args, **kwargs)
        del results['points']

        return results

    @staticmethod
    def get_metadata(data_folder, label_args, **kwargs):

        results = dict()
        contents = listdir(data_folder)
        if label_args == 'none':
            data_paths = [os.path.join(data_folder, i) for i in contents]
            results['data_paths'] = data_paths

        else:
            label_format = label_args['label_format']
            if label_format == 'image':
                contents = [i for i in contents if os.path.isdir(os.path.join(data_folder, i))]
                data_paths = [get_content(os.path.join(data_folder, i, 'data')) for i in contents]
                label_paths = [get_content(os.path.join(data_folder, i, 'label')) for i in contents]
                labels = [read_tif_as_tensor(i) for i in label_paths]
                results['data_paths'] = data_paths
                results['point_labels'] = labels
            elif label_format == 'folder':
                all_labels = [i for i in contents if os.path.isdir(os.path.join(data_folder, i))]
                data_paths = list()
                labels = list()
                for label in all_labels:
                    subcontents = listdir(os.path.join(data_folder, label))
                    data_paths.extend([os.path.join(data_folder, label, i) for i in subcontents])
                    labels.extend([label for i in subcontents])
                    results['data_paths'] = data_paths
                    results['point_labels'] = labels
            elif label_format == 'csv':
                csvpath = label_args['csv_path']
                contents = [i for i in contents if not i.endswith('.csv')]
                data_paths = [os.path.join(data_folder, i) for i in contents]
                label_dict = get_labels_from_csv(csvpath)
                labels = [label_dict[i.split('.')[0]] for i in contents]
                results['data_paths'] = data_paths
                results['point_labels'] = labels
            else:
                raise SystemExit('Error: label_type must be one of [\'none\', \'pixel\', \'point\'].')

        return results

    @staticmethod
    def get_raw_data(results, input_format, **kwargs):
        # Loading the data
        if input_format == 'single_tiff':
            points = [loadpoint_tiff_single(i) for i in results['data_paths']]
        elif input_format == 'multi_tiff':
            points = [loadpoint_tiff_multi(i) for i in results['data_paths']]
        elif input_format == 'tiff_folder':
            points = [loadpoint_tiff_folder(i) for i in results['data_paths']]
        elif input_format == 'mat_file':
            points = [loadpoint_mat_file(i) for i in results['data_paths']]
        else:
            raise SystemExit('Error: input_format must be one of [\'tiff\', \'multitiff\', \'tifffolder\', \'matfile\']')

        if 'point_labels' in results:
            for i in range(len(points)):
                points[i]['point_label'] = results['point_labels'][i]
        results['points'] = points

        return results

    @staticmethod
    def reorganize_data(results, output_format, label_args, **kwargs):
        # Organize the data

        results['samples'] = list()
        results['sources'] = list()
        if label_args != 'none':
            results['raw_labels'] = list()

        if 'field' in kwargs:
            field = kwargs['field']
        else:
            field = 'counts'

        if 'markers' in kwargs:
            use_markers = kwargs['markers']
        else:
            use_markers = results['points'][0]['markers']

        if output_format == 'point':
            for point in results['points']:
                array, markers = get_tensor_from_dict(point, field, markers=use_markers)
                results['samples'].append(array.unsqueeze(0))
                results['sources'].append(point['path'])
                if label_args != 'none':
                    results['raw_labels'].append(point['point_label'])

        elif output_format == 'marker':
            for point in results['points']:
                for marker in use_markers:
                    results['samples'].append(torch.tensor(point[field][marker]).unsqueeze(0).unsqueeze(0).float())
                    results['sources'].append(point['path'] + ':' + marker)
                    if label_args != 'none':
                        results['raw_labels'].append(point['point_label'])

        elif output_format == 'pixel':
            for point in results['points']:
                array, markers = get_tensor_from_dict(point, field, markers=use_markers)
                for i in range(array.shape[1]):
                    for j in range(array.shape[2]):
                        results['samples'].append(array[:, i, j].unsqueeze(0))
                        results['sources'].append(point['path'] + ':[' + str(i) + ',' + str(j) + ']')
                        if label_args != 'none':
                            label_format = label_args['label_format']
                            if label_format == 'image':
                                results['raw_labels'].append(point['point_label'][i, j])
                            else:
                                results['raw_labels'].append(point['point_label'])

        else:
            raise SystemExit('Error: output_format must be one of [\'point\', \'marker\', \'pixel\']')

        return results

    @staticmethod
    def organize_labels(results, label_args, **kwargs):
        if label_args == 'none':
            pass
        else:
            if label_args['label_type'] == 'regression':
                results['labels'] = results['raw_labels']
            elif label_args['label_type'] == 'categorical':
                label_dict = label_args['label_dict']
                if label_args['label_format'] == 'image':
                    results['labels'] = [PointReader.labelify(i) for i in results['raw_labels']]
                elif label_args['label_format'] == 'folder':
                    results['labels'] = [PointReader.labelify(i) for i in results['raw_labels']]
                elif label_args['label_format'] == 'csv':
                    if isinstance(results['raw_labels'][0], str):
                        results['labels'] = [PointReader.labelify(i) for i in results['raw_labels']]
                    else:
                        results['labels'] = [PointReader.labelify(i) for i in results['raw_labels']]
                n_classes = len(label_dict.keys())
                results['labels_onehot'] = [torch.zeros(1, n_classes).scatter_(1, i, 1) for i in results['labels']]
        return results

    @staticmethod
    def labelify(i):
        return torch.tensor(i).unsqueeze(0).unsqueeze(0).long()


class MIBIDataLoader:
    def __init__(self, data_folder, input_format, output_format, label_args, **kwargs):
        self.data = PointReader.load_data(data_folder, input_format, output_format, label_args, **kwargs)
        self.num_samples = len(self.data['samples'])

        if torch.cuda.is_available():
            self.device = 'cuda'
        else:
            self.device = 'cpu'

        self.cropable = dict()
        for field in self.data:
            example = self.data[field][0]
            if torch.is_tensor(example):
                if example.dim() <= 2:
                    self.cropable[field] = False
                else:
                    self.cropable[field] = True
            else:
                self.cropable[field] = False

        self.any_cropable = False
        self.return_fields = None
        self.data_shape = None
        self.crop = None
        self.stride = None
        self._sample_queue = None
        self._v_width = None
        self._v_height = None
        self._vidxmax = None

    def rename_field(self, old_field, new_field):
        self.data[new_field] = self.data.pop(old_field)
        self.cropable[new_field] = self.cropable.pop(old_field)

    def set_return_fields(self, return_fields):
        self.return_fields = return_fields
        self.any_cropable = False
        for field in return_fields:
            if self.cropable[field]:
                self.any_cropable = True
                self.data_shape = self.data[field][0].size()
        if not self.any_cropable:
            print('No fields are cropable, setting crop and stride to None')
            self.set_crop(None)
            self.set_stride(None)

    def get_samples(self, idxs):
        samples = dict()
        for field in self.return_fields:
            samples[field] = self._get_items(field, idxs).to(self.device)
        return samples

    def set_crop(self, crop):
        if self.any_cropable:
            self.crop = crop
        else:
            if crop is not None:
                print('No fields are cropable')
        self._calculate_vdims()

    def set_stride(self, stride):
        if self.any_cropable:
            self.stride = stride
        else:
            if stride is not None:
                print('No fields are cropable')
        self._calculate_vdims()

    def get_next_minibatch(self, minibatch_size):
        sample_idxs = self._get_next_minibatch_idxs(minibatch_size)
        if sample_idxs is None:
            return None
        else:
            return self.get_samples(sample_idxs)

    def pickle(self, filepath):
        pickling_on = open(filepath, 'wb')
        pickle.dump(self, pickling_on, protocol=4)
        pickling_on.close()

    @staticmethod
    def depickle(filepath):
        pickle_off = open(filepath, 'rb')
        dataset = pickle.load(pickle_off)
        return dataset

    def _crop_image(self, image, x, y):
        return image[:, :, x:(x + self.crop), y:(y + self.crop)]

    def _rotate(self, image, a):
        if a == 0:
            return image
        elif a == 1:
            return image.transpose(2, 3)
        elif a == 2:
            return image.flip(2)
        else:
            return image.transpose(2, 3).flip(3)

    def _calculate_vdims(self):
        if self.crop is None or self.stride is None:
            self._v_width = 1
            self._v_height = 1
            self._vidxmax = 1
        else:
            self._v_width = np.floor((self.data_shape[2]-self.crop)/self.stride).astype(int)+1
            self._v_height = np.floor((self.data_shape[3]-self.crop)/self.stride).astype(int)+1
            self._vidxmax = self._v_width * self._v_height

    # virtual index to subscript
    def _vidx2sub(self, index):
        y = np.floor(index / self._v_width).astype(int)
        x = (index - self._v_width * y).astype(int)
        y = y * self.stride
        x = x * self.stride
        return x, y

    def __len__(self):
        return self.num_samples

    def __getitem__(self, idx):
        return self.data['samples'][idx]

    def _get_item(self, field, rot, idx):
        sample_idx = np.floor(idx / self._vidxmax).astype(int)
        if self.cropable[field]:
            vidx = idx % self._vidxmax
            x, y = self._vidx2sub(vidx)
            cropped = self._crop_image(self.data[field][sample_idx], x, y)
            return self._rotate(cropped, rot)
        else:
            return self.data[field][sample_idx]

    def _get_items(self, field, idxs):
        randrot = [randint(0, 4) for i in idxs]
        items = torch.cat([self._get_item(field, randrot[i], idxs[i]) for i in range(len(idxs))], dim=0)
        return items

    def prepare_epoch(self):
        self._sample_queue = np.random.permutation(int(self._vidxmax * self.num_samples))

    def get_epoch_length(self):
        return int(self._vidxmax * self.num_samples)

    def get_point_indices(self, sample_indices):
        point_indices = np.floor(sample_indices / self._vidxmax).astype(int)
        return point_indices

    def _get_next_minibatch_idxs(self, minibatch_size):
        if len(self._sample_queue) == 0:  # there is nothing left in the minibatch queue
            return None
        # we WOULD return the last of the samples, but sometimes it's a small minibatch and that could fuck up batchnorm
        elif len(self._sample_queue)<minibatch_size:
            return None
        else:  # we return a normal minibatch
            minibatch_idxs = np.copy(self._sample_queue[0:minibatch_size])
            self._sample_queue = self._sample_queue[minibatch_size:]
            return minibatch_idxs

