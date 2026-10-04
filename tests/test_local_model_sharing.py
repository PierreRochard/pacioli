"""The local model shares Ollama through its scheduler instead of requiring it idle."""
import pytest

from pacioli.functions import chase_local_model as module
from pacioli.functions.chase_local_bridge import LocalChaseError


def model(monkeypatch, tmp_path, free_mib, loaded):
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *a, **k: '{}\n'.format(free_mib))
    instance = module.LocalModel()

    def api(path, data=None):
        if path == 'ps':
            return {'models': loaded}
        if path == 'tags':
            return {'models': [{'name': module.MODEL, 'digest': module.DIGEST}]}
        raise AssertionError(path)
    instance.api = api
    return instance


def test_another_ollama_model_is_swapped_not_refused(monkeypatch, tmp_path):
    other = [{'name': 'qwen3:8b', 'size_vram': 5252 * 1024 * 1024}]
    instance = model(monkeypatch, tmp_path, 8000, other)
    assert instance.reclaimable_mib() == 5252
    instance.resources()  # 8000 free + 5252 reclaimable >= 9000


def test_memory_outside_ollama_still_blocks(monkeypatch, tmp_path):
    instance = model(monkeypatch, tmp_path, 3000, [{'name': 'qwen3:8b', 'size_vram': 1024 * 1024 * 1024}])
    with pytest.raises(LocalChaseError, match='memory_pressure'):
        instance.resources()


def test_own_model_is_not_counted_as_reclaimable(monkeypatch, tmp_path):
    instance = model(monkeypatch, tmp_path, 8000, [{'name': module.MODEL, 'size_vram': 9 * 1024 ** 3}])
    assert instance.reclaimable_mib() == 0
    with pytest.raises(LocalChaseError, match='memory_pressure'):
        instance.resources()
