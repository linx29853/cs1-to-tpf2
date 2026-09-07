import tempfile
from pathlib import Path
import struct
import unittest
import numpy as np

from converter.crp import Reader, Package, ConversionError, Cancelled, Entry
from converter.scene import transform, reconstruct
from converter.exporter import convert_file, lua, install_mod


class ReaderTests(unittest.TestCase):
    def test_truncated_array_fails_without_allocation(self):
        with self.assertRaises(ConversionError):
            Reader(struct.pack('<i', 5_000_000)).array(3)

    def test_long_utf8_string_uses_7bit_length(self):
        text = '中文' * 60
        raw = text.encode('utf-8')
        encoded = bytearray(); length = len(raw)
        while length >= 128:
            encoded.append((length & 127) | 128); length >>= 7
        encoded.append(length)
        self.assertEqual(Reader(bytes(encoded)+raw).string(), text)

    def test_wrong_magic_and_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/'bad.crp'; file.write_bytes(b'CRAP')
            with self.assertRaises(ConversionError):
                Package(file)
            file.write_bytes(b'NOPE'+b'\x00'*60)
            with self.assertRaises(ConversionError):
                Package(file)

    def test_untyped_decoy_does_not_become_link(self):
        self.assertEqual(Package.read_links(b'foo\x0bm_lodObject\x04evil'), {})

    def test_lua_escape_preserves_unicode_and_control(self):
        self.assertEqual(lua('楼"\n\\'), '"楼\\"\\010\\\\"')
        with self.assertRaises(ConversionError):
            lua(float('nan'))


class GeometryTests(unittest.TestCase):
    def fixture(self, materials=1, missing=False):
        mesh = Entry(0, 'mesh', 'mesh', b'', type='UnityEngine.Mesh')
        mesh.values = {'arrays': {'vertices': np.array([[0., 0, 0], [1., 0, 0], [0., 1, 0]]),
            'normals': np.array([[0., 0, 1]]*3), 'uv': np.array([[0., 0], [1., 0], [0., 1]]), 'bones': []},
            'triangles': [np.array([0, 1, 2])]}
        material = Entry(1, 'material', 'material', b'', type='UnityEngine.Material')
        obj = Entry(2, 'root', 'root', b'', type='UnityEngine.GameObject', asset_name='Building')
        obj.values = {'components': {'UnityEngine.Transform': dict(position=[2, 0, 0],
             rotation=[0, 0, np.sin(np.pi/4), np.cos(np.pi/4)], scale=[2, 3, 4]),
             'UnityEngine.MeshFilter': 'missing' if missing else 'mesh',
             'UnityEngine.MeshRenderer': ['material']*materials}, 'links': {}}
        package = object.__new__(Package)
        package.entries = [mesh, material, obj]
        package.by_hash = {e.checksum: e for e in package.entries}
        return package, obj

    def test_transform_normals_and_mirrored_winding(self):
        package, root = self.fixture()
        near, far = reconstruct(package, root, set())
        part = near[0]
        self.assertTrue(np.allclose(part.vertices, [[2, 0, 0], [2, 0, 2], [-1, 0, 0]]))
        self.assertTrue(np.allclose(part.normals, [[0, 1, 0]]*3))
        face = part.faces[0][0]; v = part.vertices[face]
        self.assertGreater(np.dot(np.cross(v[1]-v[0], v[2]-v[0]), part.normals[0]), 0)
        self.assertIs(far[0], near[0])

    def test_extra_materials_keep_last_submesh_layers(self):
        package, root = self.fixture(materials=2)
        near, _ = reconstruct(package, root, set())
        self.assertEqual(len(near[0].faces), 2)
        self.assertTrue(np.array_equal(near[0].faces[0], near[0].faces[1]))

    def test_missing_dependency_fails(self):
        package, root = self.fixture(missing=True)
        with self.assertRaisesRegex(ConversionError, '缺少包内依赖'):
            reconstruct(package, root, set())

    def test_cycle_fails(self):
        package, root = self.fixture()
        root.values['links']['m_subMeshes'] = [dict(reference='root', position=[0, 0, 0], angle=0)]
        with self.assertRaisesRegex(ConversionError, '循环'):
            reconstruct(package, root, set())

    def test_invalid_scale(self):
        with self.assertRaises(ConversionError):
            transform(scale=(0, 1, 1))


class OutputTests(unittest.TestCase):
    def test_failure_leaves_no_partial_mod(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'broken.crp'; path.write_bytes(b'WRONG')
            output = Path(directory)/'result'
            with self.assertRaises(ConversionError):
                convert_file(path, output)
            self.assertEqual(list(output.iterdir()), [])

    def test_installer_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, game = base/'sample_1', base/'game'
            source.mkdir(); game.mkdir()
            (source/'mod.lua').write_text('example')
            (game/'TransportFever2.exe').write_bytes(b'fixture only')
            target = install_mod(source, game)
            self.assertEqual((target/'mod.lua').read_text(), 'example')
            with self.assertRaises(ConversionError):
                install_mod(source, game)
            self.assertEqual((target/'mod.lua').read_text(), 'example')


if __name__ == '__main__':
    unittest.main()
