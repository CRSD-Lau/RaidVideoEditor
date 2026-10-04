"""Exercise installed cuDNN dispatch in a child so native aborts are observable."""

import importlib.metadata
import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows wheel DLL loading")
def test_optional_cudnn_api_loads_from_registered_wheel() -> None:
    try:
        importlib.metadata.version("nvidia-cudnn-cu12")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("Optional GPU wheel is not installed")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ctypes, importlib.metadata as m; "
            "from raid_editor.highlights._intelligence_runtime import LocalWhisper; "
            "runtime = LocalWhisper.__new__(LocalWhisper); "
            "runtime._dll_handles = []; runtime._loaded_libraries = []; "
            "runtime.diagnostics = []; runtime._register_windows_dlls(); "
            "assert not runtime.diagnostics, runtime.diagnostics; "
            "path = m.distribution('nvidia-cudnn-cu12').locate_file("
            "'nvidia/cudnn/bin/cudnn64_9.dll'); "
            "library = ctypes.WinDLL(str(path)); "
            "library.cudnnGetVersion.restype = ctypes.c_size_t; "
            "assert library.cudnnGetVersion() >= 90000; "
            "runtime._close_dll_handles()",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
