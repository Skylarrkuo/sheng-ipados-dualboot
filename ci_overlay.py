"""Expose checkpoint boundaries in the pinned UniXNU recipes. Local release uses all phases."""
from pathlib import Path
import shutil


REVIEWED_SYMBOLS = Path(__file__).resolve().parent / "patches/fileset-24A446-sheng-symbols.json"


def configure_ci(core, edit):
    layout = core / "kits/XnuPrelink/fileset_layout.py"
    if layout.is_file():  # The root-only checkout omits the prelink kit.
        shutil.copyfile(REVIEWED_SYMBOLS, layout.parent / "fixtures" / REVIEWED_SYMBOLS.name)
        edit(layout, [
            ("raise ValueError('Native code would collide with collection data')",
             "raise ValueError(f'Native code end {code_end:#x} exceeds collection data at '"
             " f'{self.roots[\"__DATA\"][\"va\"]:#x} by {code_end - self.roots[\"__DATA\"][\"va\"]} bytes')"),
            ("raise ValueError(f'Native {segment[\"name\"]} overlaps a retained segment')",
             "conflicts = [(hex(b), hex(e)) for b, e in self.occupied + placed if begin < e and b < end]\n"
             "                raise ValueError(f'Native {segment[\"name\"]} {begin:#x}..{end:#x} overlaps {conflicts}')"),
        ])
    edit(core / "scripts/build-boot-runtime.py", [
        ("plugin = build_plugin(request['target'])",
         "plugin = (ROOT / 'build/adreno-metal-plugin27'\n"
         "                              if request['target'] == 'ios27' and os.environ.get('SHENG_CI_GPU_READY') == '1'\n"
         "                              else build_plugin(request['target']))"),
    ])
    edit(core / "scripts/unixnu/kernel.py", [
        ("def build_archives(ctx, env):", "def build_archives(ctx, env, phase='all'):"),
        ("        if kind == 'component':\n            spec = entry",
         "        reference = kind == 'script' and shlex.split(entry)[0] == 'build-fileset-release-reference.py'\n"
         "        if (phase == 'reference' and not reference) or (phase == 'archives' and reference):\n"
         "            continue\n        if kind == 'component':\n            spec = entry"),
        ("def build(ctx, modules):", "def build(ctx, modules, phase='all'):"),
        ("    record = ctx.tree / 'build/xnu/ios-obj/",
         "    if phase == 'reference':\n        build_archives(ctx, env, phase)\n        return\n"
         "    if phase == 'link':\n"
         "        run(['bash', script], cwd=ctx.tree, env=env, log_file=ctx.logs / 'kernel.log')\n"
         "        return\n    record = ctx.tree / 'build/xnu/ios-obj/"),
        ("    build_archives(ctx, env)\n", "    build_archives(ctx, env, phase)\n    if phase == 'archives':\n        return\n"),
    ])
