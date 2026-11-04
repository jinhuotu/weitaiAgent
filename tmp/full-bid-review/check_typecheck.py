from pathlib import Path
import subprocess
import re

root = Path(__file__).resolve().parent
staged = root / 'proposed/frontend/src/views/TendersView.vue'
before = root / 'baseline/frontend/src/views/TendersView.vue'
compiler = r'E:\code\python\weitaiAgentui\node_modules\.bin\vue-tsc.cmd'
config = root / 'proposed/frontend/tsconfig.app.json'
proposed = staged.read_bytes()

def run():
    proc = subprocess.run([compiler, '--noEmit', '-p', str(config)], capture_output=True,
                          text=True, encoding='utf-8', errors='replace')
    return proc.stdout + proc.stderr

try:
    staged.write_bytes(before.read_bytes())
    baseline = run()
finally:
    staged.write_bytes(proposed)
current = run()
normalize = lambda value: sorted(re.sub(r'^.*?error ', 'error ', line) for line in value.splitlines() if 'error TS' in line)
assert set(normalize(current)) <= set(normalize(baseline)), (baseline, current)
(root / 'typecheck-baseline.txt').write_text(baseline, encoding='utf-8')
(root / 'typecheck-proposed.txt').write_text(current, encoding='utf-8')
print(f'Standard typecheck: baseline {len(normalize(baseline))}, proposed {len(normalize(current))}; zero added diagnostics.')
