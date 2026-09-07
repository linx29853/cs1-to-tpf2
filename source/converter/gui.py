from pathlib import Path
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk
from .crp import Cancelled, ConversionError
from .exporter import convert_file, install_mod


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('CS1 → TpF2 建筑转换器 · 1.0')
        self.geometry('1120x800')
        self.minsize(960, 720)
        self.configure(bg='#f2f5fa')
        self.option_add('*Font', ('Microsoft YaHei UI', 10))
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#f2f5fa')
        style.configure('TButton', padding=(12, 7))
        style.configure('Accent.TButton', background='#2563eb', foreground='white', padding=(20, 10))
        style.map('Accent.TButton', background=[('active', '#1d4ed8'), ('disabled', '#a5b4ca')])
        style.configure('TEntry', fieldbackground='white', padding=6)
        style.configure('Treeview', rowheight=32, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'), padding=7)
        style.map('Treeview', background=[('selected', '#dbeafe')], foreground=[('selected', '#172554')])
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.items = {}
        self.photo = None
        home = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
        self.output = tk.StringVar(value=str(home/'converted'))
        self.author = tk.StringVar()
        self.preview = tk.BooleanVar(value=True)
        self.keep_obj = tk.BooleanVar(value=True)
        self.lod = tk.StringVar(value='350')
        self.status = tk.StringVar(value='添加建筑资产，然后开始转换。')
        self.controls = []
        self.create_widgets()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def button(self, parent, text, command, **kwargs):
        button = ttk.Button(parent, text=text, command=command, **kwargs)
        self.controls.append(button)
        return button

    def create_widgets(self):
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='城市建筑，带到另一座世界', font=('Microsoft YaHei UI', 20, 'bold'),
                  foreground='#15253f').pack(anchor='w')
        ttk.Label(outer, text='Cities: Skylines 1  →  Transport Fever 2     /     本地离线 · 保留原始比例和面数',
                  foreground='#64748b').pack(anchor='w', pady=(6, 18))
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill='x')
        self.button(toolbar, '添加 CRP 文件', self.add_files).pack(side='left')
        self.button(toolbar, '添加文件夹', self.add_folder).pack(side='left', padx=8)
        self.button(toolbar, '移除选中', self.remove).pack(side='left')
        self.button(toolbar, '使用说明', self.help).pack(side='right')

        body = ttk.Panedwindow(outer, orient='horizontal')
        body.pack(fill='both', expand=True, pady=12)
        left = ttk.Frame(body)
        body.add(left, weight=3)
        self.tree = ttk.Treeview(left, columns=('name', 'state', 'triangles'), show='headings', selectmode='extended', height=5)
        for key, label, width in [('name', '建筑文件', 310), ('state', '状态', 120), ('triangles', '三角面', 88)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=65, stretch=key=='name', anchor='w')
        scrollbar = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self.show_details)
        self.tree.tag_configure('error', foreground='#b91c1c')
        self.tree.tag_configure('success', foreground='#166534')
        right = ttk.Frame(body, padding=(16, 0, 0, 0), width=290)
        body.add(right, weight=1)
        ttk.Label(right, text='模型预览', font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w')
        preview_frame = ttk.Frame(right, height=170)
        preview_frame.pack(fill='both', expand=True, pady=(8, 6))
        preview_frame.pack_propagate(False)
        self.preview_label = ttk.Label(preview_frame, text='转换完成后，选择一行查看预览。', anchor='center', background='#e6edf5')
        self.preview_label.place(relwidth=1, relheight=1)
        self.detail = tk.Text(right, height=4, wrap='word', relief='flat', bg='#f2f5fa', fg='#475569',
                              font=('Microsoft YaHei UI', 9), state='disabled')
        self.detail.pack(fill='x')
        install_button = self.button(right, '安装选中结果到游戏…', self.install)
        install_button.pack(side='bottom', fill='x', pady=(6, 0), before=preview_frame)
        self.detail.pack_configure(side='bottom', before=preview_frame)

        settings = ttk.LabelFrame(outer, text='输出设置', padding=12)
        settings.pack(fill='x')
        ttk.Label(settings, text='保存目录').grid(row=0, column=0, sticky='w')
        entry = ttk.Entry(settings, textvariable=self.output)
        entry.grid(row=0, column=1, columnspan=4, sticky='ew', padx=10)
        self.controls.append(entry)
        self.button(settings, '浏览…', self.browse_output).grid(row=0, column=5)
        ttk.Label(settings, text='原作者署名').grid(row=1, column=0, sticky='w', pady=(8, 0))
        entry = ttk.Entry(settings, textvariable=self.author, width=24)
        entry.grid(row=1, column=1, sticky='w', padx=10, pady=(8, 0)); self.controls.append(entry)
        ttk.Label(settings, text='可选；批量文件使用同一署名', foreground='#64748b').grid(row=1, column=2, sticky='w', pady=(8, 0))
        ttk.Label(settings, text='LOD 切换（米）').grid(row=1, column=3, sticky='e', pady=(8, 0))
        spin = ttk.Spinbox(settings, from_=1, to=2499, textvariable=self.lod, width=7)
        spin.grid(row=1, column=4, padx=10, pady=(8, 0)); self.controls.append(spin)
        checks = ttk.Frame(settings)
        checks.grid(row=2, column=0, columnspan=6, sticky='w', pady=(9, 0))
        for text, variable in [('生成贴图预览', self.preview), ('保留可编辑 OBJ', self.keep_obj)]:
            control = ttk.Checkbutton(checks, text=text, variable=variable)
            control.pack(side='left', padx=(0, 20)); self.controls.append(control)
        settings.columnconfigure(2, weight=1)
        scope_note = ttk.Label(outer, text='范围：建筑网格、子建筑和原有 LOD。独立道具 / 树木及游戏逻辑不迁移；详情写入转换报告。',
                  foreground='#64748b', font=('Microsoft YaHei UI', 9))
        scope_note.pack(anchor='w', pady=(10, 5))
        self.logbox = tk.Text(outer, height=3, bg='#17253b', fg='#dce7f5', relief='flat', wrap='word',
                              font=('Microsoft YaHei UI', 9), padx=10, pady=8, state='disabled')
        self.logbox.pack(fill='x')
        bottom = ttk.Frame(outer)
        bottom.pack(fill='x', pady=(14, 0))
        self.button(bottom, '开始转换', self.start, style='Accent.TButton').pack(side='left')
        self.cancel_button = ttk.Button(bottom, text='取消', command=self.stop_event.set, state='disabled')
        self.cancel_button.pack(side='left', padx=8)
        self.button(bottom, '打开输出目录', self.open_output).pack(side='right')
        ttk.Label(bottom, textvariable=self.status, foreground='#475569').pack(side='left', padx=10)
        self.progress = ttk.Progressbar(outer, mode='determinate')
        self.progress.pack(fill='x', pady=(12, 0))
        # Reserve actions/settings before allocating remaining height to the resizable panes.
        # This keeps the conversion button visible on scaled 1080p Windows desktops.
        for widget in (self.progress, bottom, self.logbox, scope_note, settings):
            widget.pack_configure(side='bottom', before=body)

    def add(self, paths):
        existing = {str(item['path']).casefold() for item in self.items.values()}
        for path in paths:
            path = Path(path).resolve()
            if path.suffix.lower() == '.crp' and str(path).casefold() not in existing:
                iid = self.tree.insert('', 'end', values=(path.name, '等待', '—'))
                self.items[iid] = dict(path=path, reports=[], state='pending')
                existing.add(str(path).casefold())
        self.status.set(f'已添加 {len(self.items)} 个文件')

    def add_files(self):
        self.add(filedialog.askopenfilenames(title='选择 CS1 建筑资产', filetypes=[('CS1 资产', '*.crp')]))

    def add_folder(self):
        path = filedialog.askdirectory(title='选择资产文件夹（含子文件夹）')
        if path:
            files = sorted(Path(path).rglob('*.crp'))
            self.add(files)
            if not files:
                messagebox.showinfo('没有资产', '所选文件夹中没有找到 .crp 文件。')

    def remove(self):
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.items.pop(iid, None)
        self.show_details()

    def browse_output(self):
        path = filedialog.askdirectory(title='选择转换结果保存目录')
        if path:
            self.output.set(path)

    def set_busy(self, busy):
        self.busy = busy
        for control in self.controls:
            control.configure(state='disabled' if busy else 'normal')
        self.cancel_button.configure(state='normal' if busy else 'disabled')

    def start(self):
        todo = [(iid, item['path']) for iid, item in self.items.items() if item['state'] != 'success']
        if not todo:
            messagebox.showinfo('准备转换', '请先添加 CRP 文件。已完成的文件不会重复转换。')
            return
        try:
            distance = int(self.lod.get())
            if not 1 <= distance < 2500:
                raise ValueError()
            if not self.output.get().strip():
                raise ValueError()
        except ValueError:
            messagebox.showerror('检查输出设置', '请填写保存目录，并将 LOD 距离设为 1～2499 的整数。')
            return
        options = dict(preview=self.preview.get(), obj=self.keep_obj.get(), lod_distance=distance, author=self.author.get())
        output = self.output.get()
        self.stop_event.clear()
        self.set_busy(True)
        self.progress.configure(maximum=len(todo), value=0)
        def work():
            def check():
                if self.stop_event.is_set():
                    raise Cancelled('已取消，未完成的临时文件已清理')
            success = failed = 0
            try:
                for index, (iid, path) in enumerate(todo):
                    if self.stop_event.is_set():
                        break
                    self.events.put(('running', iid, index+1, len(todo)))
                    try:
                        reports = convert_file(path, output, options, lambda text: self.events.put(('log', text)), check)
                        self.events.put(('success', iid, reports)); success += 1
                    except Cancelled as exc:
                        self.events.put(('cancelled', iid, str(exc))); break
                    except Exception as exc:
                        self.events.put(('error', iid, str(exc))); failed += 1
                        self.events.put(('log', traceback.format_exc()))
            finally:
                self.events.put(('done', success, failed))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                typ = event[0]
                if typ == 'log':
                    self.append_log(event[1])
                elif typ == 'running':
                    _, iid, index, total = event
                    self.tree.set(iid, 'state', '转换中…')
                    self.tree.selection_set(iid)
                    self.status.set(f'正在转换 {index} / {total}')
                elif typ in ('success', 'error', 'cancelled'):
                    iid, result = event[1:]
                    item = self.items[iid]
                    item['state'] = typ
                    if typ == 'success':
                        item['reports'] = result
                        count = sum(r['triangles'] for r in result)
                        self.tree.set(iid, 'triangles', f'{count:,}')
                        self.tree.set(iid, 'state', '完成（有提示）' if any(r['warnings'] for r in result) else '完成')
                        self.tree.item(iid, tags=('success',))
                    else:
                        item['error'] = result
                        self.tree.set(iid, 'state', '已取消' if typ == 'cancelled' else '失败')
                        self.tree.item(iid, tags=('error',))
                        self.append_log(item['path'].name+'：'+result)
                    self.progress.configure(value=min(float(self.progress['value'])+1, float(self.progress['maximum'])))
                    self.show_details()
                elif typ == 'done':
                    self.set_busy(False)
                    self.status.set(f'完成 {event[1]} 个 · 失败 {event[2]} 个' + (' · 已取消' if self.stop_event.is_set() else ''))
                elif typ == 'installed':
                    self.set_busy(False)
                    self.cancel_button.configure(state='disabled')
                    self.status.set('安装处理完成')
                    messagebox.showinfo('安装结果', event[1])
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def append_log(self, text):
        self.logbox.configure(state='normal')
        self.logbox.insert('end', text+'\n'); self.logbox.see('end')
        if int(self.logbox.index('end-1c').split('.')[0]) > 1000:
            self.logbox.delete('1.0', '300.0')
        self.logbox.configure(state='disabled')

    def show_details(self, _event=None):
        selected = self.tree.selection()
        text = ''
        self.preview_label.configure(image='', text='选择已完成的建筑查看预览。')
        self.photo = None
        if selected and selected[0] in self.items:
            item = self.items[selected[0]]
            if item['reports']:
                report = item['reports'][0]
                image = Image.open(Path(report['folder'])/'preview.png')
                image.thumbnail((240, 170))
                self.photo = ImageTk.PhotoImage(image)
                self.preview_label.configure(image=self.photo, text='')
                text = f"{report['title']}\n{report['parts']} 个部件 · {report['triangles']:,} 三角面\n"
                text += '尺寸：'+' × '.join(f'{v:.1f}' for v in report['size_metres'])+' m\n'
                text += '\n'.join(report['warnings'])
            else:
                text = item.get('error', str(item['path']))
        self.detail.configure(state='normal'); self.detail.delete('1.0', 'end'); self.detail.insert('1.0', text)
        self.detail.configure(state='disabled')

    def install(self):
        reports = [r for iid in self.tree.selection() for r in self.items[iid]['reports']]
        if not reports:
            messagebox.showinfo('选择结果', '请先选中至少一行已完成的转换结果。')
            return
        game = filedialog.askdirectory(title='选择包含 TransportFever2.exe 的游戏目录')
        if not game:
            return
        if not (Path(game)/'TransportFever2.exe').is_file():
            messagebox.showerror('目录不正确', '此目录不包含 TransportFever2.exe。'); return
        self.set_busy(True)
        self.cancel_button.configure(state='disabled')
        self.status.set('正在安装选中结果…')
        def work():
            results = []
            for report in reports:
                try:
                    dest = install_mod(report['folder'], game)
                    results.append('已安装：'+report['title'])
                    self.events.put(('log', '已安装到 '+str(dest)))
                except Exception as exc:
                    results.append(report['title']+'：'+str(exc))
            self.events.put(('installed', '\n'.join(results)))
        threading.Thread(target=work, daemon=True).start()

    def open_output(self):
        path = Path(self.output.get())
        if path.is_dir():
            os.startfile(path)
        else:
            messagebox.showinfo('输出目录', '转换完成后将自动创建输出目录。')

    def help(self):
        messagebox.showinfo('使用说明',
            '1. 添加 .crp 文件，或添加文件夹进行批量转换。\n'
            '2. 选择输出位置，点击“开始转换”。\n'
            '3. 选择完成的行，查看预览与提示。\n'
            '4. 点击“安装选中结果到游戏”，选择游戏根目录。\n'
            '5. 在游戏中启用对应模组，在造景菜单查找建筑。\n\n'
            '无需安装 CS1、Blender 或 Python。所有处理在本机完成。\n'
            '原文件不会修改，同名结果和已安装模组不会覆盖。\n'
            '首版保留原始面数和已有 LOD；没有自动减面。\n'
            '只迁移静态建筑。树木、独立道具、动画与游戏逻辑不迁移。\n'
            '特殊着色器或缺失的建筑依赖会报错；具体提示见 report.json。')

    def close(self):
        if self.busy:
            messagebox.showinfo('任务进行中', '请先取消转换并等待清理结束；安装任务请等待完成。')
            return
        self.destroy()


def main():
    App().mainloop()
