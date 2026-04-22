import os

path = r'd:\Senior 2\Layout_Project\Automation\new_main\AI-Based-Analog-Layout-Automation\symbolic_editor\device_tree.py'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Split into lines
lines = content.split('\n')

# The first 5 lines are broken: '"""', '"""', 'Device Tree...', 'terminal...', '"""'
# Replace with proper docstring
new_header_lines = [
    '# -*- coding: utf-8 -*-',
    '"""',
    'Device Tree Panel -- left sidebar showing device hierarchy and',
    'terminal connectivity.',
    '"""',
]

# Skip first 5 lines (indexes 0-4), keep the rest
new_lines = new_header_lines + lines[5:]
new_content = '\n'.join(new_lines)

with open(path, 'w', encoding='utf-8', newline='\n') as f:
    f.write(new_content)

print('Fixed device_tree.py header successfully')
