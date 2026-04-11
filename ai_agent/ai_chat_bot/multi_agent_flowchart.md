# LayoutCopilot Multi-Agent Workflow

This flowchart illustrates the step-by-step process of how a user's natural language request is handled by the different specialized LLM agents in the LayoutCopilot framework.

```mermaid
flowchart TD
    %% User Input
    User([User Natural Language Input]) --> Classifier

    %% Classification
    subgraph Gatekeeper
        Classifier{Classifier Agent}
    end

    %% Routing
    Classifier -->|Concrete Request\ne.g., 'Swap M1 and M2'| CodeGen
    Classifier -->|Abstract Request\ne.g., 'Improve CMRR'| Analyzer

    %% Abstract Pipeline
    subgraph Abstract Request Processor
        Analyzer[Analyzer Agent]
        KB[(Analog Knowledge Base\n& DRC Constraints)]
        Refiner[Solution Refiner Agent]
        UserChat([User Chat Interface])
        Adapter[Solution Adapter Agent]
        
        Analyzer <-->|Reads Rules| KB
        Analyzer -->|Generates High-Level Blueprint| Refiner
        Refiner -->|Presents formatted options| UserChat
        UserChat -->|User Approves / Modifies\ne.g., 'Yes, apply symmetry'| Adapter
        Adapter -->|Extracts explicit devices from Netlist\ne.g., M34 and M35| CodeGen
    end

    %% Concrete Pipeline
    subgraph Concrete Request Processor
        CodeGen[Code Generator Agent]
    end

    %% Execution
    CodeGen -->|Generates strict JSON CMD| Executor[GUI Layout Execution]
    Executor -->|Updates Canvas| Done([Done])
    
    %% Styling
    classDef agent fill:#f9f0ff,stroke:#cc99ff,stroke-width:2px,color:#000
    classDef io fill:#e1f5fe,stroke:#03a9f4,stroke-width:2px,color:#000
    classDef system fill:#fff3e0,stroke:#ffb74d,stroke-width:2px,color:#000
    
    class Classifier,Analyzer,Refiner,Adapter,CodeGen agent
    class User,UserChat,Done io
    class KB,Executor system
```
