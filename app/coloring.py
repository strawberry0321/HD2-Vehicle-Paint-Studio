"""Hue replacement shared by face baking and native texture edits."""
from compute_runtime import np

LUMA = np.array([.2126, .7152, .0722])

def linear(rgb):
    rgb = np.asarray(rgb, dtype=float)
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)

def srgb(rgb):
    rgb = np.clip(rgb, 0, 1)
    return np.where(rgb <= .0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - .055)

def recolor_linear(source, target_srgb, reference_srgb):
    """Keep luminance detail without retaining the source yellow/red hue."""
    source = np.maximum(np.asarray(source, dtype=float), 0)
    reference_luma = max(.01, float(linear(reference_srgb) @ LUMA))
    shade = np.clip(.45 + .55 * (source @ LUMA / reference_luma) ** .55, .45, 1.6)
    return np.clip(shade[..., None] * linear(target_srgb), 0, 1)

def recolor_srgb(source, target_srgb, reference_srgb):
    return srgb(recolor_linear(linear(source), target_srgb, reference_srgb))
