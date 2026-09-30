"""Command line surface.

Output is in Chinese because the tools this interoperates with (CC Switch,
Cockpit) are Chinese-market software and so are their users. Identifiers, file
names and paths stay verbatim.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import doctor, identity, launch, shim, surface
from .home import Home, resolve_home, write_atomic
from .store import KINDS, Store
from .toml_doc import TomlError

__version__ = "0.2.0"


def _use_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _active_path(store: Store) -> Path:
    return store.root / "active.json"


def read_active(store: Store) -> str | None:
    path = _active_path(store)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("profile")
    except (ValueError, OSError):
        return None


def write_active(store: Store, name: str | None) -> None:
    write_atomic(_active_path(store), json.dumps({"profile": name}, indent=2).encode())


# --------------------------------------------------------------------------


def cmd_status(args, store: Store, home: Home) -> int:
    active = read_active(store)
    print(f"Codex home      {home.path}")
    print(f"Profile store   {store.root}")
    print(f"Active profile  {active or '(未设置)'}")
    auth = home.read_auth()
    print(f"Credential      {identity.describe(auth)}")
    config = home.read_config()
    if not config:
        print("Surface         (config.toml 不存在)")
    else:
        provider = surface.provider_of(config)
        declared = surface.declared_providers(config)
        print(f"Endpoint        model_provider = {provider or '(未设置，默认 openai)'}")
        print(f"Relay 声明      {', '.join(declared) if declared else '(无)'}")
        found = doctor.footprints(home.path)
        if found:
            for vendor, names in found.items():
                print(f"写入者足迹      {vendor}: {len(names)} 个文件")
    if identity.is_mixed(auth):
        print("警告            auth.json 同时含 ChatGPT 登录与 API Key（已被串设置）")
    return 0


def cmd_doctor(args, store: Store, home: Home) -> int:
    findings = doctor.run(home.path)
    for finding in findings:
        print(finding.render())
    code = doctor.exit_code(findings)
    fails = sum(1 for f in findings if f.level == doctor.FAIL)
    warns = sum(1 for f in findings if f.level == doctor.WARN)
    print(f"\n{len(findings)} 项检查：{fails} 失败 / {warns} 警告")
    return code


def cmd_list(args, store: Store, home: Home) -> int:
    names = store.names()
    if not names:
        print("还没有档案。先 capture 一个。")
        return 0
    active = read_active(store)
    width = max(len(n) for n in names)
    for name in names:
        profile = store.get(name)
        mark = "*" if name == active else " "
        print(f"{mark} {name.ljust(width)}  {profile.kind.ljust(8)}  {profile.label}  {profile.note}")
    return 0


def cmd_capture(args, store: Store, home: Home) -> int:
    profile = store.capture(
        args.name, home, args.kind, note=args.note or "",
        overwrite=args.overwrite,
    )
    print(f"已保存档案 {profile.name}（{profile.kind}）：{profile.label}")
    print(f"  位置 {profile.path}")
    return 0


def cmd_use(args, store: Store, home: Home) -> int:
    backup = store.use(args.name, home, keep_api_key=args.keep_api_key)
    write_active(store, args.name)
    print(f"已切换到 {args.name}；切换前状态备份为 {backup}")
    print("提示：已在运行的应用需要退出后重启才会读到新的凭据。")
    return 0


def cmd_diff(args, store: Store, home: Home) -> int:
    print(store.diff(args.name, home))
    return 0


def cmd_exec(args, store: Store, home: Home) -> int:
    """Apply the active profile, then launch the real program."""
    argv = list(args.command)
    active = read_active(store)
    if active and store.has(active):
        store.use(active, home)
    if not argv:
        argv = ["codex"]
    try:
        return launch.launch(argv, home, exclude=shim.shim_dir(store.root))
    except (FileNotFoundError, ValueError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 3


def cmd_run(args, store: Store, home: Home) -> int:
    if not store.has(args.name):
        print(f"没有名为 {args.name} 的档案。", file=sys.stderr)
        return 1
    store.use(args.name, home, keep_api_key=args.keep_api_key)
    write_active(store, args.name)
    argv = list(args.command) or ["codex"]
    try:
        return launch.launch(argv, home, exclude=shim.shim_dir(store.root))
    except (FileNotFoundError, ValueError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 3


def cmd_env(args, store: Store, home: Home) -> int:
    env = launch.clean_env(None, home.read_config())
    removed = [n for n in doctor.SHADOWING_ENV if n in __import__("os").environ and n not in env]
    keys = sorted(launch.declared_env_keys(home.read_config()))
    print(f"CODEX_HOME={home.path}")
    print(f"清理掉的变量：{', '.join(removed) if removed else '(无)'}")
    print(f"保留的中转 env_key：{', '.join(keys) if keys else '(无)'}")
    return 0


def cmd_backup(args, store: Store, home: Home) -> int:
    print(store.backup_now(home, args.label or ""))
    return 0


def cmd_backups(args, store: Store, home: Home) -> int:
    rows = store.backups()
    if not rows:
        print("还没有备份。")
        return 0
    for row in rows:
        print(f"{row['id']}  auth={row['auth']}  {row['label']}")
    return 0


def cmd_restore(args, store: Store, home: Home) -> int:
    pre = store.restore(args.backup_id, home)
    print(f"已恢复 {args.backup_id}；恢复前状态备份为 {pre}")
    return 0


def cmd_remove(args, store: Store, home: Home) -> int:
    store.remove(args.name)
    if read_active(store) == args.name:
        write_active(store, None)
    print(f"已删除档案 {args.name}")
    return 0


def cmd_shim(args, store: Store, home: Home) -> int:
    if args.action == "install":
        path = shim.install(store.root, name=args.name)
        folder = path.parent
        print(f"已生成 {path}")
        if not shim.on_path(folder):
            print(f"把这个目录加到 PATH 最前面：{folder}")
            print("之后直接运行 codex，就会自动套用当前 active 档案。")
        return 0
    if args.action == "uninstall":
        removed = shim.uninstall(store.root, name=args.name)
        print("已移除 " + ", ".join(str(p) for p in removed) if removed else "没有找到本工具生成的 shim。")
        return 0
    path = shim.shim_dir(store.root) / (f"{args.name}.cmd" if sys.platform.startswith("win") else args.name)
    print(f"shim 路径 {path}")
    print(f"是否已安装 {path.is_file()}；是否在 PATH 上 {shim.on_path(path.parent)}")
    return 0


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="settingfix",
        description="让 CC Switch / Cockpit 等账号切换器不再互相污染同一个 Codex 配置。",
    )
    parser.add_argument("--version", action="version", version=f"settingfix {__version__}")
    parser.add_argument("--home", help="Codex 目录（默认取 $CODEX_HOME，再默认 ~/.codex）")
    parser.add_argument("--store", help="档案与备份目录（默认 $SETTINGFIX_HOME，再默认 ~/.settingfix）")

    sub = parser.add_subparsers(dest="command_name", required=True)

    sub.add_parser("status", help="当前凭据与认证面").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="取证式体检").set_defaults(func=cmd_doctor)
    sub.add_parser("list", help="列出档案").set_defaults(func=cmd_list)

    cap = sub.add_parser("capture", help="把当前状态存成一个档案")
    cap.add_argument("name")
    cap.add_argument("--kind", choices=KINDS, default="relay",
                     help="official = 只保留 ChatGPT 登录并清掉中转面；relay = 完整保留")
    cap.add_argument("--note", default="")
    cap.add_argument("--overwrite", action="store_true")
    cap.set_defaults(func=cmd_capture)

    use = sub.add_parser("use", help="切换到某个档案")
    use.add_argument("name")
    use.add_argument("--keep-api-key", action="store_true",
                     help="保留当前 auth.json 里的 API Key（默认不保留）")
    use.set_defaults(func=cmd_use)

    diff = sub.add_parser("diff", help="对比档案与当前状态")
    diff.add_argument("name")
    diff.set_defaults(func=cmd_diff)

    run = sub.add_parser("run", help="切换后启动程序")
    run.add_argument("name")
    run.add_argument("--keep-api-key", action="store_true")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=cmd_run)

    ex = sub.add_parser("exec", help="套用 active 档案后启动（供 shim 调用）")
    ex.add_argument("command", nargs=argparse.REMAINDER)
    ex.set_defaults(func=cmd_exec)

    sub.add_parser("env", help="启动时会怎么改环境变量").set_defaults(func=cmd_env)

    bk = sub.add_parser("backup", help="备份当前状态")
    bk.add_argument("--label", default="")
    bk.set_defaults(func=cmd_backup)

    sub.add_parser("backups", help="列出备份").set_defaults(func=cmd_backups)

    rs = sub.add_parser("restore", help="恢复备份")
    rs.add_argument("backup_id")
    rs.set_defaults(func=cmd_restore)

    rm = sub.add_parser("remove", help="删除档案")
    rm.add_argument("name")
    rm.set_defaults(func=cmd_remove)

    sh = sub.add_parser("shim", help="安装 codex 包装脚本")
    sh.add_argument("action", choices=["install", "uninstall", "status"])
    sh.add_argument("--name", default="codex")
    sh.set_defaults(func=cmd_shim)

    return parser


def main(argv: list[str] | None = None) -> int:
    _use_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", None)
    if command and command[0] == "--":
        args.command = command[1:]

    store = Store(args.store)
    home = Home(resolve_home(args.home))
    try:
        return args.func(args, store, home)
    except TomlError as exc:
        print(f"配置有问题：{exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError, NotADirectoryError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
