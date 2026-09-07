"""CS1 to TpF2: GUI by default, --cli for reproducible batch conversion."""
import argparse
import json
from pathlib import Path
import sys


def cli(args):
    from converter.exporter import convert_file
    parser = argparse.ArgumentParser(description='CS1 CRP → TpF2 静态建筑摆件')
    parser.add_argument('inputs', nargs='+', help='CRP 文件或递归扫描的文件夹')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--no-preview', action='store_true')
    parser.add_argument('--no-obj', action='store_true')
    parser.add_argument('--author', default='')
    parser.add_argument('--lod-distance', type=int, default=350)
    parser.add_argument('--report', type=Path, help='保存批量任务 JSON 结果')
    options = parser.parse_args(args)
    files = []
    for item in options.inputs:
        path = Path(item)
        if path.is_dir():
            files.extend(sorted(path.rglob('*.crp')))
        else:
            files.append(path)
    files = list(dict.fromkeys(p.resolve() for p in files))
    results = []
    def log(message):
        if sys.stdout:
            print(message, flush=True)
    if not files:
        log('没有找到 CRP 文件')
        return 1
    for path in files:
        try:
            reports = convert_file(path, options.output,
                dict(preview=not options.no_preview, obj=not options.no_obj,
                     author=options.author, lod_distance=options.lod_distance), log)
            results.append(dict(file=str(path), status='success', reports=reports))
        except Exception as exc:
            log(f'失败：{path.name} — {exc}')
            results.append(dict(file=str(path), status='error', error=str(exc)))
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return int(any(r['status'] == 'error' for r in results))


if __name__ == '__main__':
    if '--cli' in sys.argv:
        args = sys.argv[1:]; args.remove('--cli')
        raise SystemExit(cli(args))
    from converter.gui import main
    main()
