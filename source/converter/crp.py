"""Read CS1 packages without loading game code.

Serialization reference: tony56a/crp-parser (MIT), see THIRD_PARTY.txt.
All reads are bounded by the individual package entry. No embedded code executes.
"""
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import io
import re
import struct

import numpy as np
from PIL import Image


class ConversionError(Exception):
    pass


class Cancelled(ConversionError):
    pass


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ConversionError(f"资源数据不完整（偏移 {self.pos}，需要 {size} 字节）")
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def number(self, fmt):
        return struct.unpack('<' + fmt, self.read(struct.calcsize('<' + fmt)))[0]

    def string(self):
        size = 0
        for shift in range(0, 35, 7):
            value = self.number('B')
            size |= (value & 127) << shift
            if value < 128:
                return self.read(size).decode('utf-8', errors='strict')
        raise ConversionError('字符串长度编码无效')

    def count(self, limit=1_000_000):
        value = self.number('i')
        if not 0 <= value <= limit:
            raise ConversionError(f'不支持的数组大小：{value}')
        return value

    def vector(self, count):
        return list(struct.unpack('<' + 'f' * count, self.read(4 * count)))

    def array(self, components, integer=False):
        count = self.count(5_000_000)
        return np.frombuffer(self.read(count * components * 4),
                             dtype='<i4' if integer else '<f4').reshape(count, components).copy()


@dataclass
class Entry:
    index: int
    name: str
    checksum: str
    raw: bytes
    type: str = ''
    asset_name: str = ''
    values: dict = field(default_factory=dict)
    error: str = ''


class Package:
    def __init__(self, path, check=lambda: None):
        self.path = Path(path)
        if self.path.stat().st_size > 512 * 1024 * 1024:
            raise ConversionError('首版支持 512 MB 以内的建筑资产包')
        data = self.path.read_bytes()
        self.sha256 = hashlib.sha256(data).hexdigest()
        reader = Reader(data)
        if reader.read(4) != b'CRAP':
            raise ConversionError('不是支持的 CS1 CRP 文件（文件标记不匹配）')
        self.version = reader.number('H')
        self.package_name = reader.string()
        reader.string()  # encoded author metadata
        reader.number('I')
        self.main_name = reader.string()
        count = reader.count(100_000)
        base = reader.number('Q')
        headers = []
        for index in range(count):
            check()
            name, checksum = reader.string(), reader.string()
            reader.number('I')
            offset, size = reader.number('Q'), reader.number('Q')
            if base + offset + size > len(data):
                raise ConversionError(f'资源 {index} 超出原始文件范围')
            headers.append((index, name, checksum, base + offset, size))
        if reader.pos > base:
            raise ConversionError('CRP 索引与数据区重叠')
        self.entries = []
        self.by_hash = {}
        for index, name, checksum, offset, size in headers:
            check()
            entry = Entry(index, name, checksum, data[offset:offset + size])
            self.entries.append(entry)
            if checksum in self.by_hash:
                if self.by_hash[checksum].raw != entry.raw:
                    raise ConversionError('包内存在冲突的资源校验标识')
            self.by_hash[checksum] = entry
            try:
                self.parse_entry(entry)
            except (ConversionError, UnicodeError, ValueError, struct.error) as exc:
                entry.error = str(exc)

    def parse_entry(self, entry):
        reader = Reader(entry.raw)
        if reader.number('B') != 0:
            return  # package preview or metadata, not an object
        entry.type = reader.string().split(',')[0]
        entry.asset_name = reader.string()
        if entry.type == 'UnityEngine.Mesh':
            arrays = {}
            for key, count in [('vertices', 3), ('colors', 4), ('uv', 2),
                               ('normals', 3), ('tangents', 4), ('bones', 8), ('bindposes', 16)]:
                arrays[key] = reader.array(count)
            triangles = [reader.array(1, True).ravel() for _ in range(reader.count(256))]
            vertices = arrays['vertices']
            if not len(vertices) or not np.isfinite(vertices).all():
                raise ConversionError('网格缺少有效顶点')
            for key in ('uv', 'normals'):
                if len(arrays[key]) not in (0, len(vertices)) or not np.isfinite(arrays[key]).all():
                    raise ConversionError(f'网格 {key} 数量或数值异常')
            for faces in triangles:
                if len(faces) % 3 or (len(faces) and (faces.min() < 0 or faces.max() >= len(vertices))):
                    raise ConversionError('三角面索引无效')
            if reader.pos != len(entry.raw):
                raise ConversionError('网格包含当前版本尚不支持的尾部数据')
            entry.values = dict(arrays=arrays, triangles=triangles)
        elif entry.type == 'UnityEngine.Material':
            shader = reader.string()
            properties = {}
            for _ in range(reader.count(4096)):
                typ, key = reader.number('i'), reader.string()
                if typ in (0, 1):
                    value = reader.vector(4)
                elif typ == 2:
                    value = reader.number('f')
                elif typ == 3:
                    value = None if reader.number('B') else reader.string()
                else:
                    raise ConversionError(f'不支持的材质属性类型：{typ}')
                properties[key] = value
            if reader.pos != len(entry.raw):
                raise ConversionError('材质包含当前版本尚不支持的尾部数据')
            entry.values = dict(shader=shader, properties=properties)
        elif entry.type == 'UnityEngine.GameObject':
            tag, layer, enabled = reader.string(), reader.number('i'), reader.number('B')
            components = {}
            remainder = ''
            for _ in range(reader.count(256)):
                if reader.number('B'):
                    continue
                typ = reader.string().split(',')[0]
                if typ == 'UnityEngine.Transform':
                    value = dict(position=reader.vector(3), rotation=reader.vector(4), scale=reader.vector(3))
                elif typ == 'UnityEngine.MeshFilter':
                    value = reader.string()
                elif typ == 'UnityEngine.MeshRenderer':
                    value = [reader.string() for _ in range(reader.count(256))]
                elif typ.endswith('InfoGen'):
                    value = reader.string()
                else:
                    remainder = typ
                    break
                components[typ] = value
            entry.values = dict(components=components, component=remainder, layer=layer,
                                enabled=enabled, links=self.read_links(entry.raw))

    @staticmethod
    def read_links(raw):
        """Read typed rendering references inside serialized BuildingInfo fields.

        Match type declaration AND property name, not arbitrary texture/string bytes.
        Gameplay fields remain uninterpreted and are never executed.
        """
        links = {}
        expected = {'m_subMeshes': 'BuildingInfo+MeshInfo[]',
                    'm_subBuildings': 'BuildingInfo+SubInfo[]',
                    'm_lodObject': 'UnityEngine.GameObject',
                    'm_props': 'BuildingInfo+Prop[]'}
        for key, typ in expected.items():
            token = bytes([len(key)]) + key.encode()
            candidates = []
            for match in re.finditer(re.escape(token), raw):
                start = max(0, match.start() - 220)
                prefix = raw[start:match.start()]
                if typ.encode() + b',' in prefix and prefix.endswith(b'null'):
                    candidates.append(match.end())
            if not candidates:
                continue
            if len(candidates) != 1:
                raise ConversionError(f'资源中存在多义的 {key} 引用')
            reader = Reader(raw)
            reader.pos = candidates[0]
            if key == 'm_lodObject':
                links[key] = reader.string()
                continue
            count = reader.count(4096)
            if key == 'm_props':
                links[key] = count
                continue
            values = []
            for _ in range(count):
                value = {'reference': reader.string()}
                if key == 'm_subMeshes':
                    value.update(forbidden=reader.number('i'), required=reader.number('i'))
                value.update(position=reader.vector(3), angle=reader.number('f'))
                if key == 'm_subBuildings':
                    value['fixed_height'] = reader.number('B')
                values.append(value)
            links[key] = values
        return links

    def require(self, reference, typ):
        entry = self.by_hash.get(reference)
        if entry is None:
            matches = [e for e in self.entries if e.type == typ and
                       (e.name == reference or e.asset_name == reference)]
            if len(matches) == 1:
                entry = matches[0]
        if entry is None:
            raise ConversionError(f'缺少包内依赖：{reference}。请提供包含依赖的原始资产；首版不会静默丢弃部件。')
        if entry.type != typ or entry.error:
            raise ConversionError(f'无法读取 {entry.name}：{entry.error or "资源类型不匹配"}')
        return entry

    def image(self, reference):
        entry = self.require(reference, 'UnityEngine.Texture2D')
        for signature in (b'DDS ', b'\x89PNG\r\n\x1a\n'):
            start = entry.raw.find(signature)
            if start >= 0:
                try:
                    image = Image.open(io.BytesIO(entry.raw[start:]))
                    if image.width * image.height > 67_108_864:
                        raise ConversionError('贴图尺寸超过首版限制')
                    image.load()
                    return image
                except (OSError, ValueError) as exc:
                    raise ConversionError(f'贴图 {entry.name} 无法解码：{exc}') from exc
        raise ConversionError(f'贴图 {entry.name} 不是支持的 DDS/PNG 格式')

    def roots(self):
        candidates = [e for e in self.entries if e.type == 'UnityEngine.GameObject'
                      and e.values.get('component') == 'BuildingInfo' and not e.error]
        children = set()
        for entry in candidates:
            for link in entry.values['links'].get('m_subBuildings', []):
                children.add(self.require(link['reference'], 'UnityEngine.GameObject').index)
        roots = [e for e in candidates if e.index not in children]
        if not roots:
            problems = [e.error for e in self.entries if e.type == 'UnityEngine.GameObject' and e.error]
            raise ConversionError('没有找到可转换的建筑主对象。' + ('；'.join(problems[:3])))
        return roots
