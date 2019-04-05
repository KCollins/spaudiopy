# -*- coding: utf-8 -*-
"""
@author: chris
"""

import os
from warnings import warn

import numpy as np
import pandas as pd
from scipy.io import loadmat, wavfile
import h5py

import soundfile as sf

from . import utils, sig, decoder, sdm


def load_audio(filenames, fs=None):
    """Load mono and multichannel audio from files.

    Parameters
    ----------
    filenames : string or list of strings
        Audio files.

    Returns
    -------
    sig : sig.MonoSignal or sig.MultiSignal
        Audio signal.
    """
    loaded_data = []
    loaded_fs = []
    # pack in list if only a single string
    if not isinstance(filenames, (list, tuple)):
        filenames = [filenames]
    for file in filenames:
        data, fs_file = sf.read(file)
        if data.ndim != 1:
            # detect and split interleaved wav
            for c in data.T:
                loaded_data.append(c)
        else:
            loaded_data.append(data)
        loaded_fs.append(fs_file)
    # Assert same sample rate for all channels
    assert all(x == loaded_fs[0] for x in loaded_fs)
    # Check against provided samplerate
    if fs is not None:
        if fs != loaded_fs[0]:
            raise ValueError("File: Found different fs:" + str(loaded_fs[0]))
    else:
        fs = loaded_fs[0]
    # MonoSignal or MultiSignal
    if len(loaded_data) == 1:
        return sig.MonoSignal(loaded_data, fs=fs)
    else:
        return sig.MultiSignal([*loaded_data], fs=fs)


def save_audio(signal, filename, fs=None):
    """Save signal to audio file.

    Parameters
    ----------
    signal : sig. MonoSignal, sig.MultiSignal or np.ndarray
        Audio Signal, forwarded to sf.write()/
    filename : string
        Audio file name.
    fs : int
        fs(t).
    """
    # assert(isinstance(signal, (sig.MonoSignal, sig.MultiSignal)))
    if isinstance(sig, sig.MonoSignal):
        if fs is not None:
            assert(signal.fs == fs)

    if type(signal) == sig.MonoSignal:
        data = signal.signal
        data_fs = signal.fs
    elif type(signal) == sig.MultiSignal:
        data = signal.get_signals().T
        data_fs = signal.fs
    else:
        data = signal
        data_fs = fs

    sf.write(filename, data, data_fs)


def load_hrirs(fs, filename=None, dummy=False):
    """Convenience function to load HRTF.mat.

    Parameters
    ----------
    fs : int
        fs(t).
    filename : string, optional
        HRTF.mat file or default set.
    dummy : bool, optional
        Returns dummy hrirs (debugging).

    Returns
    -------
    HRIRs : sig.HRIRs instance
        left : (g, h) numpy.ndarray
            h(t) for grid position g.
        right : (g, h) numpy.ndarray
            h(t) for grid position g.
        grid : (g, 2) pandas.dataframe
            [azi: azimuth, colat: colatitude] for hrirs.
        fs : int
            fs(t).
    """
    if filename is None:
        if fs == 44100:
            default_file = '../data/HRTF_default.mat'
        elif fs == 48000:
            default_file = '../data/HRTF_default48k.mat'
        else:
            raise ValueError("No default hrirs.")
        current_file_dir = os.path.dirname(__file__)
        filename = os.path.join(current_file_dir, default_file)

    try:
        mat = loadmat(filename)
    except FileNotFoundError:
        raise ValueError("No default hrirs. Try running HRIRs_from_SH.py")

    hrir_l = np.array(np.squeeze(mat['hrir_l']), dtype=float)
    hrir_r = np.array(np.squeeze(mat['hrir_r']), dtype=float)
    hrir_fs = int(mat['SamplingRate'])
    azi = np.array(np.squeeze(mat['azi']), dtype=float)
    elev = np.array(np.squeeze(mat['elev']), dtype=float)
    grid = pd.DataFrame({'azi': azi, 'colat': elev})
    if dummy is True:
        # Create diracs as dummy
        hrir_l = np.zeros_like(hrir_l)
        hrir_l[:, 0] = np.ones(hrir_l.shape[0])
        hrir_r = np.zeros_like(hrir_r)
        hrir_r[:, 0] = np.ones(hrir_r.shape[0])

    HRIRs = sig.HRIRs(hrir_l, hrir_r, grid, hrir_fs)
    assert HRIRs.fs == fs
    return HRIRs


def load_sdm(filename, init_nan=True):
    """Convenience function to load SDM.mat.

    Parameters
    ----------
    filename : string
        SDM.mat file
    init_nan : bool, optional
        Initialize nan to [0, pi/2].

    Returns
    -------
    h : (n,) array_like
        p(t).
    sdm_phi : (n,) array_like
        Azimuth angle.
    sdm_theta : (n,) array_like
        Colatitude angle.
    fs : int
        fs(t).
    """
    mat = loadmat(filename)
    h = np.array(np.squeeze(mat['h_ref']), dtype=float)
    sdm_phi = np.array(np.squeeze(mat['sdm_phi']), dtype=float)
    sdm_theta = np.array(np.squeeze(mat['sdm_theta']), dtype=float)
    if init_nan:
        sdm_phi[np.isnan(sdm_phi)] = 0.
        sdm_theta[np.isnan(sdm_theta)] = np.pi / 2
    fs = int(mat['fs'])
    return h, sdm_phi, sdm_theta, fs


def load_sofa_data(filename):
    """Load .sofa file into python dictionary that contains the data in
    numpy arrays."""
    with h5py.File(filename, 'r') as f:
        out_dict = {}
        for key, value in f.items():
            out_dict[key] = np.squeeze(value)
    return out_dict


def write_ssr_brirs_loudspeaker(filename, ls_irs, hull, fs, hrirs=None):
    """Write binaural room impulse responses (BRIRs) and save as wav file.

    The azimuth resolution is one degree. The channels are interleaved and
    directly compatible to the SoundScape Renderer (SSR) ssr-brs.

    Parameters
    ----------
    filename : string
    ls_irs : (L, S) np.ndarray
        Impulse responses of L loudspeakers,
        e.g. by hull.loudspeaker_signals().
    hull : decoder.LoudspeakerSetup
    fs : int
    hrirs : sig.HRIRs, optional

    """
    if hrirs is None:
        hrirs = load_hrirs(fs=fs)
    assert(hrirs.fs == fs)

    if not filename[-4:] == '.wav':
        filename = filename + '.wav'

    ssr_brirs = np.zeros((720, ls_irs.shape[1] + len(hrirs) - 1))
    for angle in range(0, 360):
        ir_l, ir_r = hull.binauralize(ls_irs, fs,
                                      orientation=(np.deg2rad(angle), 0),
                                      hrirs=hrirs)
        # left
        ssr_brirs[2 * angle, :] = ir_l
        # right
        ssr_brirs[2 * angle + 1, :] = ir_r

    # normalize
    if np.max(np.abs(ssr_brirs)) > 1:
        warn('Normalizing BRIRs')
        ssr_brirs = ssr_brirs / np.max(np.abs(ssr_brirs))

    # write to file
    wavfile.write(filename, fs, ssr_brirs.astype(np.float32).T)


def write_ssr_brirs_sdm(filename, sdm_p, sdm_phi, sdm_theta, fs, hrirs=None):
    """Write binaural room impulse responses (BRIRs) and save as wav file.

    The azimuth resolution is one degree. The channels are interleaved and
    directly compatible to the SoundScape Renderer (SSR) ssr-brs.

    Parameters
    ----------
    filename : string
    sdm_p : (n,) array_like
        Pressure p(t).
    sdm_phi : (n,) array_like
        Azimuth phi(t).
    sdm_theta : (n,) array_like
        Colatitude theta(t).
    fs : int
    hrirs : sig.HRIRs, optional

    """
    if hrirs is None:
        hrirs = load_hrirs(fs=fs)
    assert(hrirs.fs == fs)

    if not filename[-4:] == '.wav':
        filename = filename + '.wav'

    ssr_brirs = np.zeros((720, len(sdm_p) + len(hrirs) - 1))
    for angle in range(0, 360):
        sdm_phi_rot = sdm_phi - np.deg2rad(angle)
        ir_l, ir_r = sdm.render_bsdm(sdm_p, sdm_phi_rot, sdm_theta,
                                     hrirs=hrirs)
        # left
        ssr_brirs[2 * angle, :] = ir_l
        # right
        ssr_brirs[2 * angle + 1, :] = ir_r

    # normalize
    if np.max(np.abs(ssr_brirs)) > 1:
        warn('Normalizing BRIRs')
        ssr_brirs = ssr_brirs / np.max(np.abs(ssr_brirs))

    # write to file
    wavfile.write(filename, fs, ssr_brirs.astype(np.float32).T)
