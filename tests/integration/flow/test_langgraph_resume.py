from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict

class State(TypedDict):
    stage: str
    count: int

def node1(state: State):
    print("Running node1")
    return {"stage": "node1", "count": state.get("count", 0) + 1}

def node2(state: State):
    print("Running node2")
    return {"stage": "node2", "count": state.get("count", 0) + 1}

workflow = StateGraph(State)
workflow.add_node("n1", node1)
workflow.add_node("n2", node2)

def router(state: State):
    return state["stage"]

workflow.add_conditional_edges(START, router, {"n1": "n1", "n2": "n2"})
workflow.add_edge("n1", END)
workflow.add_edge("n2", END)

checkpointer = MemorySaver()
app = workflow.compile(checkpointer=checkpointer)

config = {"configurable": {"thread_id": "1"}}
print("--- First invoke ---")
print(app.invoke({"stage": "n1"}, config))

print("--- Second invoke ---")
print(app.invoke({"stage": "n2"}, config))

