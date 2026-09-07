"""Small offline diffuse renderer; no Blender, GPU or network required."""
import numpy as np
from PIL import Image, ImageDraw


def render(parts, images, check=lambda: None, size=640):
    eye = np.array([1., 2., 1.])
    eye /= np.linalg.norm(eye)
    right = np.cross([0., 0., 1.], eye)
    right /= np.linalg.norm(right)
    up = np.cross(eye, right)
    basis = np.stack([right, up, eye], axis=1)
    vertices = np.concatenate([p.vertices for p in parts])
    points = vertices @ basis
    lo, hi = points[:, :2].min(0), points[:, :2].max(0)
    scale = (size-80)/max(hi-lo).clip(1e-5)
    center = (lo+hi)/2
    pixels = np.full((size, size, 3), [235, 240, 246], dtype=np.uint8)
    depth = np.full((size, size), -np.inf)
    textures = {i: np.asarray(im.convert('RGB')) for i, im in images.items()}
    light = np.array([-.3, -.5, 1.]); light /= np.linalg.norm(light)
    for part in parts:
        check()
        v = part.vertices @ basis
        screen = (v[:, :2]-center)*scale
        screen[:, 0] += size/2
        screen[:, 1] = size/2-screen[:, 1]
        for faces, material in zip(part.faces, part.materials):
            tex = textures[material.index]
            for number, face in enumerate(faces):
                if number % 128 == 0:
                    check()
                a, b, c = screen[face]
                xmin = max(0, int(np.floor(min(a[0], b[0], c[0]))))
                xmax = min(size-1, int(np.ceil(max(a[0], b[0], c[0]))))
                ymin = max(0, int(np.floor(min(a[1], b[1], c[1]))))
                ymax = min(size-1, int(np.ceil(max(a[1], b[1], c[1]))))
                if xmax < xmin or ymax < ymin:
                    continue
                denominator = (b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
                if abs(denominator) < 1e-8:
                    continue
                yy, xx = np.mgrid[ymin:ymax+1, xmin:xmax+1]
                xx, yy = xx+.5, yy+.5
                w0 = ((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/denominator
                w1 = ((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/denominator
                w2 = 1-w0-w1
                z = w0*v[face[0], 2]+w1*v[face[1], 2]+w2*v[face[2], 2]
                buffer = depth[ymin:ymax+1, xmin:xmax+1]
                mask = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6) & (z > buffer)
                if not mask.any():
                    continue
                uv = (w0[..., None]*part.uv[face[0]] + w1[..., None]*part.uv[face[1]] +
                      w2[..., None]*part.uv[face[2]])
                tx = np.floor((uv[:, :, 0] % 1)*tex.shape[1]).astype(int) % tex.shape[1]
                ty = np.floor((1-uv[:, :, 1] % 1)*tex.shape[0]).astype(int) % tex.shape[0]
                normal = part.normals[face].mean(0)
                normal /= max(np.linalg.norm(normal), 1e-8)
                shade = .70+.30*abs(np.dot(normal, light))
                color = (tex[ty, tx]*shade).clip(0, 255).astype(np.uint8)
                pixels[ymin:ymax+1, xmin:xmax+1][mask] = color[mask]
                buffer[mask] = z[mask]
    image = Image.fromarray(pixels)
    ImageDraw.Draw(image).text((18, size-24), 'Diffuse preview / original geometry', fill='#4d647c')
    return image
