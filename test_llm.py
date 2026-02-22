#from ai_agent.openai_placer import llm_generate_placement

#llm_generate_placement(
#    "xor_layout_graph.json",
#    "xor_initial_placement.json"
#)





# if you got an error related to the gemini api
# you can try to set the environment variable
# ( $env:GEMINI_API_KEY="AIzaSyApwhWPssGbI6L5siyrfn24AYQWe52NW2E" )
# then run from the terminal (python test_llm.py) 

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