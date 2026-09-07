from dataclasses import replace
from pathlib import Path
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import zipfile

import numpy as np
from PIL import Image, ImageOps
from .crp import Package, ConversionError
from .scene import reconstruct

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def lua(value, level=0):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        # JSON's unicode/control escapes are not Lua's; escape UTF-8 text explicitly.
        value = value.replace('\\', '\\\\').replace('"', '\\"')
        return '"' + ''.join(f'\\{ord(c):03d}' if ord(c) < 32 else c for c in value) + '"'
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ConversionError('不能输出包含 NaN/Infinity 的配置')
        return repr(value)
    if isinstance(value, (list, tuple)):
        return '{ ' + ', '.join(lua(v, level+1) for v in value) + ' }'
    if isinstance(value, dict):
        lines = []
        for key, val in value.items():
            name = key if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key) else '[' + lua(key) + ']'
            lines.append('  ' * (level+1) + name + ' = ' + lua(val, level+1) + ',')
        return '{\n' + '\n'.join(lines) + '\n' + '  ' * level + '}'
    raise TypeError(type(value))


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def write_table(path, value):
    write(path, 'function data()\nreturn ' + lua(value) + '\nend\n')


def map_config(ref):
    return dict(fileName=ref, type='TWOD', magFilter='LINEAR', minFilter='LINEAR_MIPMAP_LINEAR',
                wrapS='REPEAT', wrapT='REPEAT', compressionAllowed=True,
                mipmapAlphaScale=0, scaleDownAllowed=True)


def emissive(material):
    return 'additive' in material.values['shader'].lower()


def split_parts(parts, glowing):
    result = []
    for part in parts:
        pairs = [(f, m) for f, m in zip(part.faces, part.materials) if emissive(m) == glowing and len(f)]
        if pairs:
            result.append(replace(part, faces=[p[0] for p in pairs], materials=[p[1] for p in pairs]))
    return result


def tangent_space(part, uv, check):
    normals = part.normals
    tangent, bitangent = np.zeros_like(normals), np.zeros_like(normals)
    # Vectorized accumulation avoids a Python loop per triangle on large assets.
    for faces in part.faces:
        check()
        if not len(faces):
            continue
        v, t = part.vertices[faces], uv[faces]
        e1, e2 = v[:, 1]-v[:, 0], v[:, 2]-v[:, 0]
        u1, u2 = t[:, 1]-t[:, 0], t[:, 2]-t[:, 0]
        determinant = u1[:, 0]*u2[:, 1]-u1[:, 1]*u2[:, 0]
        valid = np.abs(determinant) > 1e-12
        divisor = np.where(valid, determinant, 1)[:, None]
        a = (e1*u2[:, 1, None]-e2*u1[:, 1, None])/divisor
        b = (e2*u1[:, 0, None]-e1*u2[:, 0, None])/divisor
        a[~valid], b[~valid] = 0, 0
        for corner in range(3):
            np.add.at(tangent, faces[:, corner], a)
            np.add.at(bitangent, faces[:, corner], b)
    tangent -= normals * np.sum(normals*tangent, axis=1, keepdims=True)
    bad = np.linalg.norm(tangent, axis=1) < 1e-10
    axis = np.tile([0., 0., 1.], (len(normals), 1))
    axis[np.abs(normals[:, 2]) > .9] = [0, 1, 0]
    tangent[bad] = np.cross(normals[bad], axis[bad])
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1, keepdims=True), 1e-12)
    sign = np.where(np.sum(np.cross(normals, tangent)*bitangent, axis=1) < 0, -1, 1)
    return np.column_stack([tangent, sign])


def export_mesh(part, path, check):
    uv = part.uv.copy()
    uv[:, 1] = 1-uv[:, 1]  # OBJ/Unity bottom-left -> TpF2 top-left
    attrs = dict(position=part.vertices, normal=part.normals, uv0=uv,
                 tangent=tangent_space(part, uv, check))
    data, blob = {'vertexAttr': {}, 'subMeshes': []}, bytearray()
    for key, array in attrs.items():
        raw = np.asarray(array, dtype='<f4').tobytes()
        data['vertexAttr'][key] = dict(count=len(raw), numComp=array.shape[1], offset=len(blob))
        blob.extend(raw)
    for faces in part.faces:
        raw = np.asarray(faces, dtype='<u4').tobytes()
        indices = dict(count=len(raw), offset=len(blob))
        blob.extend(raw)
        data['subMeshes'].append({'indices': {k: indices for k in attrs}})
    write_table(path, data)
    path.with_suffix('.msh.blob').write_bytes(blob)
    # Verify serialized bytes, attributes, index bounds, and triangle count.
    disk = path.with_suffix('.msh.blob').read_bytes()
    for key, spec in data['vertexAttr'].items():
        actual = np.frombuffer(disk, dtype='<f4', count=spec['count']//4,
                               offset=spec['offset']).reshape(-1, spec['numComp'])
        if not np.array_equal(actual, np.asarray(attrs[key], dtype='<f4')) or not np.isfinite(actual).all():
            raise ConversionError('网格写入校验失败：' + key)
    for sub, faces in zip(data['subMeshes'], part.faces):
        spec = sub['indices']['position']
        actual = np.frombuffer(disk, dtype='<u4', count=spec['count']//4, offset=spec['offset']).reshape(-1, 3)
        if not np.array_equal(actual, faces) or (len(actual) and actual.max() >= len(part.vertices)):
            raise ConversionError('三角面写入校验失败')


def export_obj(parts, path, material_paths):
    lines = ['# Z up, metres; original detail preserved', 'mtllib building.mtl']
    base = 1
    for part in parts:
        lines.append('o ' + part.name)
        for kind, array in [('v', part.vertices), ('vt', part.uv), ('vn', part.normals)]:
            lines.extend(kind + ' ' + ' '.join(f'{v:.8g}' for v in row) for row in array)
        for faces, mat in zip(part.faces, part.materials):
            lines.append(f'usemtl material_{mat.index:03d}')
            for face in faces:
                lines.append('f ' + ' '.join(f'{int(i)+base}/{int(i)+base}/{int(i)+base}' for i in face))
        base += len(part.vertices)
    write(path, '\n'.join(lines) + '\n')
    mtl = []
    for index, tex in sorted(material_paths.items()):
        mtl += [f'newmtl material_{index:03d}', 'Kd 1 1 1', 'illum 2', f'map_Kd ../res/textures/{tex}', '']
    write(path.parent/'building.mtl', '\n'.join(mtl))


def make_mod(package, root, folder, options, log, check):
    warnings = set()
    near, far = reconstruct(package, root, warnings, check)
    title = root.asset_name.removesuffix('_Data') or package.path.stem
    ns = 'asset/' + folder.name.removesuffix('_1')
    res = folder/'res'
    materials = {m.index: m for p in near+far for m in p.materials}
    paths, images = {}, {}
    for index, mat in sorted(materials.items()):
        check()
        shader = mat.values['shader']
        supported = shader in ('Custom/Buildings/Building/Default', 'Legacy Shaders/Diffuse') or emissive(mat)
        if not supported:
            raise ConversionError(f'首版尚不支持着色器：{shader}（{mat.name}）')
        properties = mat.values['properties']
        ref = properties.get('_MainTex')
        if ref:
            image = package.image(ref).convert('RGB')
        else:
            color = properties.get('_Color', [1, 1, 1, 1])
            image = Image.new('RGB', (4, 4), tuple(int(np.clip(v, 0, 1)*255) for v in color[:3]))
            warnings.add(f'材质 {mat.asset_name} 没有主贴图，采用其基础颜色。')
        texref = f'models/{ns}/material_{index:03d}_albedo.tga'
        destination = res/'textures'/texref
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination)
        Image.open(destination).verify()
        paths[index], images[index] = texref, image
        if emissive(mat):
            params = dict(map_emissive=map_config(texref), emissive_scale={'emissiveScale': [1., 1., 1.]})
            typ = 'EMISSIVE'
        else:
            window = any(m.index == index and re.search('窗|玻璃|window|glass', p.source_name, re.I)
                         for p in near+far for m in p.materials)
            mga = f'models/{ns}/material_{index:03d}_mga.tga'
            Image.new('RGB', (4, 4), (0, 190 if window else 55, 255)).save(res/'textures'/mga)
            params = dict(map_albedo=map_config(texref), map_metal_gloss_ao=map_config(mga),
                          albedo_scale={'albedoScale': [1., 1., 1.]})
            typ = 'PHYSICAL'
        write_table(res/'models/material'/ns/f'material_{index:03d}.mtl', dict(params=params, type=typ))

    bounds = np.concatenate([p.vertices for p in near])
    bbox = dict(bbMin=bounds.min(0).tolist(), bbMax=bounds.max(0).tolist())
    has_glow = bool(split_parts(near, True))
    model_names = []
    mesh_count = 0
    for glow in (False, True):
        if glow and not has_glow:
            continue
        if not split_parts(near, glow):
            continue
        name = 'lights' if glow else 'building'
        model_names.append(name)
        lods = []
        for lod_index, (start, end, parts) in enumerate([(0, options['lod_distance'], near),
                                                         (options['lod_distance'], 2500, far)]):
            nodes = []
            for part in split_parts(parts, glow):
                check()
                filename = f'{name}_lod{lod_index}_{part.name}.msh'
                export_mesh(part, res/'models/mesh'/ns/filename, check)
                mesh_count += 1
                nodes.append(dict(name=part.name, mesh=f'{ns}/{filename}', transf=IDENTITY,
                                  materials=[f'{ns}/material_{m.index:03d}.mtl' for m in part.materials]))
            if nodes:
                lods.append(dict(node=dict(children=nodes, transf=IDENTITY), static=False,
                                 visibleFrom=start, visibleTo=end))
        write_table(res/'models/model'/ns/(name+'.mdl'), dict(boundingInfo=bbox,
            collider=dict(params={}, transf=IDENTITY, type='MESH'), lods=lods,
            metadata={'description': dict(name=title, description='静态造景建筑')}, version=1))

    author = options.get('author', '').strip() or '原资产作者（见原始 CRP / 创意工坊）'
    write_table(folder/'mod.lua', {'info': dict(minorVersion=0, severityAdd='NONE', severityRemove='WARNING',
        name=title+'（自用摆件）', description='CS1 建筑静态转换。原作者：'+author,
        authors=[dict(name=author, role='CREATOR')], tags=['Asset', 'Building'], visible=True)})
    params = [{'key': folder.name+'_height', 'name': '高度', 'uiType': 'COMBOBOX',
               'values': ['-2 m', '-1 m', '0 m', '+1 m', '+2 m', '+3 m', '+5 m'], 'defaultIndex': 2}]
    if has_glow:
        params.append(dict(key=folder.name+'_lights', name='发光装饰', uiType='BUTTON',
                           values=['关闭', '开启'], defaultIndex=1))
    model_code = '\n'.join(
        (f'    if params[{lua(folder.name+"_lights")}] ~= 0 then\n' if n == 'lights' else '') +
        f'    result.models[#result.models+1] = {{id={lua(ns+"/"+n+".mdl")}, transf=t}}\n' +
        ('    end\n' if n == 'lights' else '') for n in model_names)
    construction = f'''function data()
return {{
  type = "ASSET_DEFAULT",
  description = {{name={lua(title)}, description="静态造景建筑"}},
  availability = {{yearFrom=0, yearTo=0}}, buildMode="MULTI",
  categories={{"buildings"}}, order=100, skipCollision=true, autoRemovable=false,
  params={lua(params)},
  updateFn=function(params)
    local offsets={{-2,-1,0,1,2,3,5}}
    local z=offsets[(params[{lua(folder.name+'_height')}] or 2)+1] or 0
    local t={{1,0,0,0,0,1,0,0,0,0,1,0,0,0,z,1}}
    local result={{models={{}},terrainAlignmentLists={{}},groundFaces={{}},cost=0,bulldozeCost=0,maintenanceCost=0}}
{model_code}
    return result
  end,
}}
end
'''
    write(res/'construction/asset'/(folder.name.removesuffix('_1')+'.con'), construction)
    log(f'{title}：{len(near)} 个部件 / {sum(sum(len(f) for f in p.faces) for p in near):,} 个三角面')
    check()
    if options.get('preview', True):
        from .preview import render
        log('生成带贴图预览…')
        preview = render(near, images, check)
    else:
        preview = Image.new('RGB', (512, 512), '#e6edf5')
    preview.save(folder/'preview.png')
    ImageOps.contain(preview, (480, 480)).save(folder/'image_00.tga')
    icon = ImageOps.contain(preview, (128, 128)).convert('RGBA')
    ip = res/'textures/ui/construction/asset'/(folder.name.removesuffix('_1')+'.tga')
    ip.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new('RGBA', (128, 128)); canvas.paste(icon, ((128-icon.width)//2, (128-icon.height)//2)); canvas.save(ip)
    if options.get('obj', True):
        export_obj(near, folder/'editable/building.obj', paths)
        export_obj(far, folder/'editable/building_lod.obj', paths)
    notes = ['主体原面数保留；没有额外减面。', '玻璃高光和静态发光采用首个实测版本的近似材质。',
             '不迁移游戏逻辑、独立道具/树木、动画、XYS/ACI 法线与夜间窗灯。']
    report = dict(version='1.0.0', title=title, source_file=str(package.path.resolve()),
                  source_sha256=package.sha256, source_root=root.index,
                  parts=len(near), triangles=sum(sum(len(f) for f in p.faces) for p in near),
                  lod_triangles=sum(sum(len(f) for f in p.faces) for p in far),
                  size_metres=(bounds.max(0)-bounds.min(0)).tolist(), meshes=mesh_count,
                  warnings=sorted(warnings), notes=notes, validation='serialized mesh roundtrip passed',
                  game_test='not run by converter', mod_id=folder.name)
    write(folder/'report.json', json.dumps(report, ensure_ascii=False, indent=2))
    write(folder/'说明.txt', title+'\n\n将本文件夹放入 Transport Fever 2/mods，启用模组后到造景菜单查找建筑。\n'
          '保持文件夹名称末尾的 _1；不要多套一层目录。\n原始文件未修改。\n\n'+
          '\n'.join(notes)+'\n\n转换提示\n'+'\n'.join(sorted(warnings))+
          '\n\n已检查输出网格与贴图，实际显示请在游戏中确认。\n')
    return report


def convert_file(path, output, options=None, log=lambda text: None, check=lambda: None):
    options = dict(preview=True, obj=True, lod_distance=350, author='', **(options or {})) if options is None else {
        **dict(preview=True, obj=True, lod_distance=350, author=''), **options}
    if not 1 <= options['lod_distance'] < 2500:
        raise ConversionError('LOD 切换距离应在 1～2499 米之间')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    log('读取 '+str(path))
    package = Package(path, check)
    roots = package.roots()
    ids = [f'cs1_{package.sha256[:12]}_{r.index}_1' for r in roots]
    for mod_id in ids:
        if (output/mod_id).exists() or (output/(mod_id+'.zip')).exists():
            raise ConversionError(f'输出已存在：{mod_id}。请选择新的输出目录，或手动移走旧结果。')
    reports = []
    # Work in a private temporary directory; failures never become installable partial mods.
    with tempfile.TemporaryDirectory(prefix='.cs1-convert-', dir=output) as temporary:
        stage = Path(temporary)
        for root, mod_id in zip(roots, ids):
            check()
            folder = stage/mod_id
            folder.mkdir()
            report = make_mod(package, root, folder, options, log, check)
            archive = stage/(mod_id+'.zip')
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                for file in sorted(folder.rglob('*')):
                    check()
                    if file.is_file():
                        z.write(file, file.relative_to(stage))
            with zipfile.ZipFile(archive) as z:
                if z.testzip() is not None:
                    raise ConversionError('安装包校验失败')
            reports.append(report)
        check()
        # Windows rename is no-clobber. Remove nothing from existing outputs.
        published = []
        try:
            for mod_id in ids:
                for name in (mod_id, mod_id+'.zip'):
                    os.rename(stage/name, output/name)
                    published.append(name)
        except OSError:
            # Roll back only this invocation's just-published files into its staging dir.
            for name in reversed(published):
                os.rename(output/name, stage/name)
            raise
    for report in reports:
        report['folder'] = str(output/report['mod_id'])
        report['zip'] = str(output/(report['mod_id']+'.zip'))
        log('完成：'+report['folder'])
    return reports


def install_mod(source, game):
    source, game = Path(source).resolve(), Path(game).resolve()
    if not (game/'TransportFever2.exe').is_file() or not (source/'mod.lua').is_file():
        raise ConversionError('请选择有效的 TpF2 游戏目录和已完成的模组')
    mods = game/'mods'
    mods.mkdir(exist_ok=True)
    dest = mods/source.name
    if dest.exists():
        raise ConversionError('游戏中已有同名模组，未覆盖：'+str(dest))
    with tempfile.TemporaryDirectory(prefix='.cs1-install-', dir=mods) as tmp:
        staged = Path(tmp)/source.name
        shutil.copytree(source, staged)
        for original in source.rglob('*'):
            if original.is_file():
                copy = staged/original.relative_to(source)
                if hashlib.sha256(copy.read_bytes()).digest() != hashlib.sha256(original.read_bytes()).digest():
                    raise ConversionError('安装文件校验失败')
        os.rename(staged, dest)
    return dest
