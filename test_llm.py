#from ai_agent.openai_placer import llm_generate_placement

#llm_generate_placement(
#    "xor_layout_graph.json",
#    "xor_initial_placement.json"
#)







from ai_agent.gemini_placer import gemini_generate_placement

gemini_generate_placement(
    "Xor_layout_graph.json",
    "Xor_initial_placement.json"
)



#from ai_agent.ollama_placer import ollama_generate_placement

#ollama_generate_placement(
#    "xor_layout_graph.json",
#    "xor_initial_placement.json",
#    model="llama3.2"
#)