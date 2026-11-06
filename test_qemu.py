#!/usr/bin/env python3
"""Pruebas de la Etapa 3: argumentos QEMU y discos qcow2.

No lanza ninguna VM. Solo construye/inspecciona la línea de comandos y usa
``qemu-img`` para crear discos en un directorio temporal.

Uso:
    python3 test_qemu.py            # Python del sistema (sin PyQt6)
    python3 test_qemu.py --debug
"""

from __future__ import annotations

import argparse
import logging
import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

OK, WARN, FAIL = "OK", "WARN", "FAIL"
Row = tuple[str, str, str]


def has_pair(args: Sequence[str], flag: str, value: str) -> bool:
    return any(
        args[index] == flag and index + 1 < len(args) and args[index + 1] == value
        for index in range(len(args))
    )


def has_prefix(args: Sequence[str], flag: str, prefix: str) -> bool:
    return any(
        args[index] == flag
        and index + 1 < len(args)
        and args[index + 1].startswith(prefix)
        for index in range(len(args))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tests de argumentos QEMU y qcow2.")
    parser.add_argument("--debug", action="store_true", help="logs DEBUG")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )

    rows: list[Row] = []

    def check(status: str, label: str, detail: str = "") -> None:
        rows.append((status, label, detail))

    with tempfile.TemporaryDirectory(prefix="vibecbox-qemu-") as tmp:
        root = Path(tmp)
        os.environ["VIBECBOX_DATA_HOME"] = str(root / "data")

        import models
        import qemu
        import vibecBox_core as core

        data_dir = root / "share" / "qemu"
        data_dir.mkdir(parents=True)
        (data_dir / "bios-256k.bin").write_bytes(b"\x00" * 16)

        iso = root / "instalacion.iso"
        iso.write_bytes(b"\x00" * 2048)

        disk = root / "discos" / "prueba.qcow2"
        disk.parent.mkdir(parents=True)
        disk.write_bytes(b"")  # existe pero vacío: create_disk lo recreará

        def vm(**overrides: object) -> models.VMConfig:
            base = models.VMConfig(
                name="Etapa 3",
                ram_mb=2048,
                vcpus=4,
                disk_gb=1,
                disk_path=str(disk),
                iso_path=str(iso),
                network="user",
                video="virtio-vga",
                boot_order="disk",
            )
            return base.copy_with(**overrides)

        qemu_path = core.require_binary(core.QEMU_SYSTEM)
        qemu_img = core.require_binary(core.QEMU_IMG)

        # ---------------------------------------------------------------- #
        # 1. Plan con KVM
        # ---------------------------------------------------------------- #
        plan = qemu.plan_launch(
            vm(),
            qemu_path=qemu_path,
            kvm_available=True,
            display="gtk",
            data_dir=data_dir,
        )
        a = plan.args
        kvm_checks = {
            "machine q35": has_pair(a, "-machine", "q35"),
            "accel kvm": has_pair(a, "-accel", "kvm"),
            "cpu host": has_pair(a, "-cpu", "host"),
            "smp 4": has_pair(a, "-smp", "4"),
            "ram 2048M": has_pair(a, "-m", "2048M"),
            "disco virtio": has_pair(a, "-device", "virtio-blk-pci,drive=disk0"),
            "red virtio": has_pair(a, "-device", "virtio-net-pci,netdev=net0"),
            "vídeo virtio": has_pair(a, "-device", "virtio-vga"),
            "boot c": has_pair(a, "-boot", "order=c,menu=on"),
            "display gtk": has_pair(a, "-display", "gtk"),
            "rng": has_pair(a, "-device", "virtio-rng-pci,rng=rng0"),
            "tablet": has_pair(a, "-device", "usb-tablet"),
            "-L": has_pair(a, "-L", str(data_dir)),
        }
        failed = [name for name, ok in kvm_checks.items() if not ok]
        check(
            OK if not failed else FAIL,
            "Plan KVM completo",
            "12/12 argumentos" if not failed else f"faltan: {', '.join(failed)}",
        )
        check(
            OK if plan.command()[0] == qemu_path and plan.accel == "kvm" else FAIL,
            "Programa y acelerador",
            f"{Path(plan.command()[0]).name} · {plan.accel}",
        )
        check(
            OK if plan.preview().startswith(qemu_path) and " -m 2048M " in plan.preview() else FAIL,
            "Vista previa",
            plan.preview()[:96] + "…",
        )
        check(
            OK if not plan.warnings else FAIL,
            "Sin avisos con KVM",
            f"{len(plan.warnings)} aviso(s)",
        )

        # ---------------------------------------------------------------- #
        # 2. Fallback TCG
        # ---------------------------------------------------------------- #
        tcg = qemu.plan_launch(vm(), qemu_path=qemu_path, kvm_available=False)
        check(
            OK
            if has_pair(tcg.args, "-accel", "tcg")
            and has_pair(tcg.args, "-cpu", "max")
            and not has_pair(tcg.args, "-cpu", "host")
            else FAIL,
            "Fallback TCG",
            "accel=tcg · cpu=max",
        )
        check(
            OK if any("TCG" in w for w in tcg.warnings) else FAIL,
            "Aviso TCG explícito",
            tcg.warnings[0] if tcg.warnings else "sin aviso",
        )

        # ---------------------------------------------------------------- #
        # 3. Orden de arranque, ISO, red y vídeo
        # ---------------------------------------------------------------- #
        boot_ok = True
        for order, code in qemu.BOOT_CODES.items():
            built = qemu.plan_launch(
                vm(boot_order=order), qemu_path=qemu_path, kvm_available=True
            )
            boot_ok &= has_pair(built.args, "-boot", f"order={code},menu=on")
        check(OK if boot_ok else FAIL, "Órdenes de arranque", "disk=c · cdrom=d · network=n")

        iso_plan = qemu.plan_launch(vm(), qemu_path=qemu_path, kvm_available=True)
        check(
            OK
            if has_prefix(iso_plan.args, "-drive", f"file={iso},")
            and has_pair(iso_plan.args, "-device", "scsi-cd,drive=cd0,bus=scsi0.0")
            and has_pair(iso_plan.args, "-device", "virtio-scsi-pci,id=scsi0")
            else FAIL,
            "ISO en CD SCSI",
            "scsi-cd sobre virtio-scsi",
        )

        check(
            OK if has_pair(iso_plan.args, "-monitor", "none") else FAIL,
            "Monitor desactivado",
            "-monitor none",
        )
        qmp_plan = qemu.plan_launch(
            vm(),
            qemu_path=qemu_path,
            kvm_available=True,
            qmp_socket=root / "run" / "prueba.qmp",
        )
        check(
            OK
            if has_prefix(qmp_plan.args, "-qmp", "unix:")
            and has_pair(qmp_plan.args, "-monitor", "none")
            else FAIL,
            "QMP opcional",
            "-qmp unix:…,server=on,wait=off",
        )

        no_iso = qemu.plan_launch(
            vm(iso_path=None), qemu_path=qemu_path, kvm_available=True
        )
        check(
            OK if not has_prefix(no_iso.args, "-device", "scsi-cd") else FAIL,
            "Sin ISO",
            "no añade CD",
        )

        none_net = qemu.plan_launch(
            vm(network="none"), qemu_path=qemu_path, kvm_available=True
        )
        check(
            OK if not has_prefix(none_net.args, "-netdev", "user") else FAIL,
            "Red desactivada",
            "sin -netdev",
        )

        bridge = qemu.plan_launch(
            vm(network="bridge"), qemu_path=qemu_path, kvm_available=True
        )
        check(
            OK
            if has_prefix(bridge.args, "-netdev", "bridge,id=net0,br=")
            and any("puente" in w for w in bridge.warnings)
            else FAIL,
            "Red puente avisada",
            bridge.warnings[0] if bridge.warnings else "sin aviso",
        )

        check(
            OK
            if has_pair(
                qemu.plan_launch(vm(video="std"), qemu_path=qemu_path).args,
                "-device",
                "VGA",
            )
            else FAIL,
            "Vídeo VGA estándar",
            "device VGA",
        )

        # qxl no está compilado en este QEMU: debe rechazarse, no generar basura.
        try:
            qemu.plan_launch(vm(video="qxl"), qemu_path=qemu_path)
        except qemu.QemuConfigError as exc:
            check(OK, "Vídeo no soportado", str(exc))
        else:
            check(FAIL, "Vídeo no soportado", "qxl aceptado por error")

        # ---------------------------------------------------------------- #
        # 4. Datos de runtime y -L
        # ---------------------------------------------------------------- #
        distro_plan = qemu.plan_launch(
            vm(), qemu_path="/usr/bin/qemu-system-x86_64", kvm_available=True
        )
        check(
            OK if not has_pair(distro_plan.args, "-L", "/usr/share/qemu") else FAIL,
            "Datos de distro",
            "sin -L (QEMU usa su prefijo)",
        )

        managed = root / "data" / "bin" / "qemu-system-x86_64"
        managed.parent.mkdir(parents=True)
        managed.write_bytes(b"\x00")
        managed.chmod(0o755)
        managed_plan = qemu.plan_launch(
            vm(),
            qemu_path=str(managed),
            kvm_available=True,
            data_dir=data_dir,
        )
        check(
            OK if has_pair(managed_plan.args, "-L", str(data_dir)) else FAIL,
            "Datos gestionados",
            f"-L {data_dir}",
        )

        # ---------------------------------------------------------------- #
        # 5. Configuraciones inválidas
        # ---------------------------------------------------------------- #
        invalid_ok = 0
        for kwargs in (
            {"ram_mb": 0},
            {"vcpus": 0},
            {"disk_gb": 0},
            {"boot_order": "floppy"},
        ):
            try:
                qemu.plan_launch(vm(**kwargs), qemu_path=qemu_path)
            except qemu.QemuConfigError:
                invalid_ok += 1
        check(
            OK if invalid_ok == 4 else FAIL,
            "Configuraciones inválidas",
            f"{invalid_ok}/4 rechazadas",
        )

        # ---------------------------------------------------------------- #
        # 6. Disco real con qemu-img
        # ---------------------------------------------------------------- #
        created = qemu.create_disk(vm(), qemu_img=qemu_img)
        magic = created.read_bytes()[:4]
        check(
            OK if created.is_file() and magic == qemu.QCOW2_MAGIC else FAIL,
            "Disco qcow2 creado",
            f"{created.name} · {created.stat().st_size} B",
        )

        info = qemu.disk_info(created, qemu_img=qemu_img)
        expected = 1 * 1024**3
        check(
            OK
            if info.get("format") == "qcow2" and info.get("virtual-size") == expected
            else FAIL,
            "qemu-img info",
            f"format={info.get('format')} · virtual-size={info.get('virtual-size')}",
        )

        again = qemu.create_disk(vm(), qemu_img=qemu_img)
        check(
            OK if again == created and again.is_file() else FAIL,
            "Creación idempotente",
            "mismo fichero, sin recrear",
        )

        forced = qemu.create_disk(vm(), qemu_img=qemu_img, force=True)
        check(
            OK if forced == created and forced.read_bytes()[:4] == qemu.QCOW2_MAGIC else FAIL,
            "Recreación forzada",
            "qcow2 válido tras force",
        )

        try:
            qemu.create_disk(
                vm(disk_path=str(root / "otro" / "sin-tool.qcow2")),
                qemu_img=str(root / "no-existe"),
            )
        except qemu.DiskError as exc:
            check(OK, "qemu-img ausente", str(exc)[:60])
        else:
            check(FAIL, "qemu-img ausente", "no lanzó DiskError")

        try:
            qemu.disk_info(root / "fantasma.qcow2", qemu_img=qemu_img)
        except qemu.DiskError as exc:
            check(OK, "Disco inexistente", str(exc)[:60])
        else:
            check(FAIL, "Disco inexistente", "no lanzó DiskError")

    # -------------------------------------------------------------------- #
    # Informe
    # -------------------------------------------------------------------- #
    width = max(len(label) for _, label, _ in rows) + 2
    line = "=" * 84
    print(line)
    print("vibecBox :: test_qemu — argumentos VirtIO y discos qcow2")
    print(line)
    for status, label, detail in rows:
        print(f"[{status:<4}] {label:<{width}} {detail}")
    print("-" * 84)

    counts = {s: sum(1 for row in rows if row[0] == s) for s in (OK, WARN, FAIL)}
    print(f"Resultado: {counts[OK]} OK · {counts[WARN]} WARN · {counts[FAIL]} FAIL")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
