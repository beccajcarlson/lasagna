from skimage import transform
from skimage.feature import register_translation
import numpy as np
import pandas as pd
import uuid

from skimage.filter import gaussian_filter, threshold_adaptive
from skimage.morphology import disk, watershed, opening
from skimage.util import img_as_uint
from skimage.measure import label, regionprops
from skimage.feature import peak_local_max
from scipy import ndimage

from lasagna import io

DOWNSAMPLE = 2

def region_fields(region):
    return {'area': region.area,
            'centroid': region.centroid,
            'bounds': region.bbox,
            'label': region.label}


def table_from_nuclei(stack_files, well_site=None):
    """
    :param stack_files: single filename or list of filenames. glob accepted.
    :param well:
    :return:
    """
    files = io.get_file_list(stack_files)
    dataframes = []
    for f in files:
        if well_site is None:
            well, site = io.get_well_site(f)
        else:
            well, site = well_site
        # load nuclei file
        data = io.read_stack(io.add_dir(f, 'nuclei'))
        region_info = [region_fields(r) for r in regionprops(data)]
        [ri.update({'file': f.replace(io.DIR['lasagna'] + '/', '',),
                    'hash': uuid.uuid4().hex}) for ri in region_info]

        index = [[well]*len(region_info), [site]*len(region_info)]
        df = pd.DataFrame(region_info, index=index)
        df = df.set_index('label', append=True)
        dataframes.append(df)
    df = pd.concat(dataframes)
    df.index.names = ['well', 'site', 'label']

    return df
    # index well, site
    # identify round with column multi-index?
    # doesn't really matter, can un-stack round from earlier dataframe
    # columns area, bounds, circularity/perimeter, relative coordinates in well?
    #





def register_images(images, index=None, window=(500, 500)):
    """Register a series of image stacks to pixel accuracy.
    :param images: list of N-dim image arrays, height and width may differ
    :param index: image[index] should yield 2D array with which to perform alignment
    :param window: centered window in which to perform registration, smaller is faster
    :return list[(int)]: list of offsets
    """
    if index is None:
        index = ((0,) * (images[0].ndim - 2) + (slice(None),) * 2)

    sz = [image[index].shape for image in images]
    sz = np.array([max(x) for x in zip(*sz)])

    origin = np.array(images[0].shape) * 0

    center = tuple([slice(s / 2 - min(s / 2, rw), s / 2 + min(s / 2, rw))
                    for s, rw in zip(sz, window)])

    def pad(img):
        pad_width = [(s / 2, s - s / 2) for s in (sz - img.shape)]
        img = np.pad(img, pad_width, 'constant')
        return img[center], [x[0] for x in pad_width]

    image0, pad_width = pad(images[0][index])
    offsets = [origin.copy()]
    offsets[0][-2:] += pad_width
    for image in [x[index] for x in images[1:]]:
        padded, pad_width = pad(image)
        shift, error, _ = register_translation(image0,
                                               padded)
        offsets += [origin.copy()]
        offsets[-1][-2:] = shift + pad_width  # automatically cast to uint64

    return offsets


class Sample(object):
    def __init__(self, rate):
        """Provides methods for downsampling and upsampling trailing XY dimensions of ndarray.
        Automatically uses original shape for upsampling.
        :param rate:
        :return:
        """
        self.rate = float(rate)
        self.sampled = {}

    def downsample(self, img, shape=None):
        """Downsample image according to Sample.rate, or shape if provided.
        :param img:
        :param shape: tuple indicating downsampled XY dimensions
        :return:
        """
        if shape is None:
            shape = tuple([int(s / self.rate) for s in img.shape[-2:]])

        new_img = np.zeros(img.shape[:-2] + shape, dtype=img.dtype)

        for idx in np.ndindex(img.shape[:-2]):
            # parameters necessary to properly transform label arrays by non-integer factors
            new_img[idx] = transform.resize(img[idx], shape, order=0,
                                            mode='nearest', preserve_range=True)

        # store correct shape for inverting
        self.sampled[(shape, self.rate)] = img.shape[-2:]

        return new_img

    def upsample(self, img, shape=None):
        if shape is None:
            s = (img.shape[-2:], self.rate)
            if s in self.sampled:
                shape = self.sampled[s]
            else:
                shape = tuple([int(s * self.rate) for s in img.shape[-2:]])

        new_img = np.zeros(img.shape[:-2] + shape, dtype=img.dtype)

        for idx in np.ndindex(img.shape[:-2]):
            # parameters necessary to properly transform label arrays by non-integer factors
            new_img[idx] = transform.resize(img[idx], shape, order=0,
                                            mode='nearest', preserve_range=True)

        return new_img


def get_nuclei(img, opening_radius=6, block_size=80, threshold_offset=0):
    s = Sample(DOWNSAMPLE)
    binary = threshold_adaptive(s.downsample(img), int(block_size / s.rate), offset=threshold_offset)
    filled = fill_holes(binary)
    opened = opening(filled, selem=disk(opening_radius / s.rate))
    nuclei = apply_watershed(opened)
    nuclei = s.upsample(nuclei)
    return img_as_uint(nuclei)


def fill_holes(img):
    labels = label(img)
    background_label = np.bincount(labels.flatten()).argmax()
    return labels != background_label


def apply_watershed(img):
    distance = ndimage.distance_transform_edt(img)
    distance = gaussian_filter(distance, 4)
    local_maxi = peak_local_max(distance, indices=False, footprint=np.ones((3, 3)))
    markers = ndimage.label(local_maxi)[0]
    return watershed(-distance, markers, mask=img).astype(np.uint16)
