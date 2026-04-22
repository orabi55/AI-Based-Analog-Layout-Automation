import ast
import os

agents_dir = r'c:\Users\DELL G3\Desktop\GP\Automation\AI-Automation-New\ai_agent\ai_chat_bot\agents'
agent_files = ['classifier.py', 'drc_critic.py', 'placement_specialist.py', 'routing_previewer.py', 'topology_analyst.py']

all_agent_functions = set()
for filename in agent_files:
    path = os.path.join(agents_dir, filename)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
            functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
            all_agent_functions.update(functions)

multi_placer_path = r'c:\Users\DELL G3\Desktop\GP\Automation\AI-Automation-New\ai_agent\ai_initial_placement\multi_agent_placer.py'
with open(multi_placer_path, 'r', encoding='utf-8') as f:
    tree = ast.parse(f.read())
    multi_placer_functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]

unique_to_multi = [f for f in multi_placer_functions if f not in all_agent_functions]

print("Functions in multi_agent_placer.py but NOT in separate agents:")
for f in sorted(unique_to_multi):
    print(f"- {f}")
