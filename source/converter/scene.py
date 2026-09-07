from dataclasses import dataclass
import numpy as np
from .crp import ConversionError


@dataclass
class Part:
    name: str
    source_name: str
    vertices: np.ndarray
    normals: np.ndarray
    uv: np.ndarray
    faces: list
    materials: list


def transform(position=(0, 0, 0), rotation=(0, 0, 0, 1), scale=(1, 1, 1)):
    q = np.asarray(rotation, dtype=float)
    length = np.linalg.norm(q)
    if length < 1e-8:
        raise ConversionError('部件旋转四元数无效')
    x, y, z, w = q / length
    result = np.eye(4)
    result[:3, :3] = np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]) @ np.diag(scale)
    result[:3, 3] = position
    if not np.isfinite(result).all() or abs(np.linalg.det(result[:3, :3])) < 1e-10:
        raise ConversionError('部件变换包含无效值或零缩放')
    return result


def link_transform(link):
    radians = np.deg2rad(link['angle']) / 2
    return transform(link['position'], (0, np.sin(radians), 0, np.cos(radians)))


def reconstruct(package, root, warnings, check=lambda: None):
    near, far = [], []
    swap = np.eye(4)[[0, 2, 1, 3]]

    def make(entry, parent, name):
        check()
        components = entry.values['components']
        mesh_ref = components.get('UnityEngine.MeshFilter')
        if not mesh_ref:
            raise ConversionError(f'部件 {entry.name} 缺少网格引用')
        mesh = package.require(mesh_ref, 'UnityEngine.Mesh')
        arrays = mesh.values['arrays']
        local = transform(**components.get('UnityEngine.Transform', {}))
        matrix = swap @ parent @ local
        vertices = arrays['vertices'] @ matrix[:3, :3].T + matrix[:3, 3]
        faces = [f.reshape(-1, 3).copy() for f in mesh.values['triangles']]
        if np.linalg.det(matrix[:3, :3]) < 0:
            faces = [f[:, ::-1] for f in faces]
        if len(arrays['normals']):
            normals = arrays['normals'] @ np.linalg.inv(matrix[:3, :3])
        else:
            normals = np.zeros_like(vertices)
            warnings.add(f'{entry.asset_name} 缺少法线，已按面重建平滑法线。')
        lengths = np.linalg.norm(normals, axis=1)
        if np.any(lengths < 1e-8):
            generated = np.zeros_like(vertices)
            for f in faces:
                cross = np.cross(vertices[f[:, 1]]-vertices[f[:, 0]], vertices[f[:, 2]]-vertices[f[:, 0]])
                for corner in range(3):
                    np.add.at(generated, f[:, corner], cross)
            bad = lengths < 1e-8
            normals[bad] = generated[bad]
            bad = np.linalg.norm(normals, axis=1) < 1e-8
            normals[bad] = [0, 0, 1]
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        uv = arrays['uv'].copy()
        if not len(uv):
            raise ConversionError(f'部件 {entry.asset_name} 缺少 UV，无法可靠恢复贴图')
        materials = [package.require(ref, 'UnityEngine.Material')
                     for ref in components.get('UnityEngine.MeshRenderer', [])]
        if len(materials) > len(faces) and faces:
            # Unity renders the last submesh again for each extra renderer material.
            faces += [faces[-1].copy() for _ in range(len(materials) - len(faces))]
            warnings.add(f'{entry.asset_name} 含多层材质，已保留重复绘制的最后子网格。')
        if len(materials) != len(faces):
            raise ConversionError(f'部件 {entry.asset_name} 的子网格与材质数量不匹配')
        if len(arrays['bones']):
            warnings.add(f'{entry.asset_name} 含骨骼数据；仅输出静态网格姿态。')
        return Part(name, entry.asset_name, vertices, normals, uv, faces, materials)

    def walk(entry, parent, stack):
        check()
        if entry.index in stack or len(stack) > 32:
            raise ConversionError('建筑部件引用形成循环或嵌套过深')
        stack = (*stack, entry.index)
        name = f'part_{len(near):03d}_obj{entry.index}'
        near.append(make(entry, parent, name))
        links = entry.values['links']
        lod_ref = links.get('m_lodObject')
        if lod_ref:
            far.append(make(package.require(lod_ref, 'UnityEngine.GameObject'), parent, name + '_lod'))
        else:
            far.append(near[-1])
            warnings.add(f'{entry.asset_name} 无独立 LOD，远景沿用主网格以保留外观。')
        if links.get('m_props', 0):
            warnings.add(f'{entry.asset_name} 的 {links["m_props"]} 个独立道具/树木布置未迁移；建筑网格和子建筑已保留。')
        base = parent @ transform(**entry.values['components'].get('UnityEngine.Transform', {}))
        for link in links.get('m_subMeshes', []) + links.get('m_subBuildings', []):
            if link.get('required', 0) or link.get('forbidden', 0):
                raise ConversionError('建筑包含条件部件，首版无法确定正确的静态组合')
            child = package.require(link['reference'], 'UnityEngine.GameObject')
            walk(child, base @ link_transform(link), stack)

    walk(root, np.eye(4), ())
    return near, far
