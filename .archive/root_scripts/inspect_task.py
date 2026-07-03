import pathlib

p = pathlib.Path(r'C:\Users\navadeep\Documents\Kryth\kryth\src\agent\task_classifier.py')
text = p.read_text(encoding='utf-8', errors='replace')

out = pathlib.Path(r'C:\Users\navadeep\Documents\Kryth\kryth\inspect_task_out.txt')
with out.open('w', encoding='utf-8') as f:
    for label, pat in [
        ('block1', '# Simple-starter words with no build verb'),
        ('block2', '# Short inputs without build verb'),
        ('block3', '# Clear simple'),
    ]:
        idx = text.find(pat)
        if idx == -1:
            f.write(f'{label}: NOT FOUND\n\n')
            continue
        chunk = text[idx:idx+800]
        f.write(f'--- {label} ---\n')
        for ln in chunk.splitlines()[:35]:
            f.write(repr(ln) + '\n')
        f.write('\n')
print('wrote', out)
