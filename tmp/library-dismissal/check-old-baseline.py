"""Re-run two failing older assertions with pre-task slot/key functions in memory."""
import inspect
import sys
from pathlib import Path

sys.path[:0] = [str(Path.cwd() / 'apps'), str(Path.cwd() / 'packages')]
import pytest
from api.services.tenders import placeholders

namespace = dict(vars(placeholders))
source = inspect.getsource(placeholders.collect_slots)
start = source.index('    from api.services.tenders.material_identity import material_key_aliases\n')
end = source.index('    base_items =', start)
source = source[:start] + source[end:]
start = source.index('    aliases = material_key_aliases(')
end = source.index('    # Supply attachment routing', start)
source = source[:start] + source[end:]
source = source.replace('        key = aliases.get(key, key)\n        if key in dismissed and key not in real_keys:\n            continue\n', '')
exec(source, namespace)
placeholders.collect_slots = namespace['collect_slots']
source = inspect.getsource(placeholders.this_bid_keys)
start = source.index('    def canonical_keys(')
end = source.index('    if material_demands is not None:', start)
source = source[:start] + source[end:]
source = source.replace('return canonical_keys(required, normalize_slot_keys([*required,*(include_keys or [])]))',
                        'return required, normalize_slot_keys([*required,*(include_keys or [])])')
source = source.replace('return canonical_keys(required, include)', 'return required, include')
exec(source, namespace)
placeholders.this_bid_keys = namespace['this_bid_keys']
raise SystemExit(pytest.main(['tests/test_tender_material_slots.py', '-k', 'record_pipeline_keeps_critical',
                             '-q', '-p', 'no:cacheprovider', '--basetemp', 'tmp/library-dismissal/pytest-baseline']))
