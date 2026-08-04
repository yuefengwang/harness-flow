from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict

class State(TypedDict):
    current_stage: str

def node1(state: State):
    print("Running node1")
    return {"current_stage": "01"}

def node2(state: State):
    print("Running node2")
    return {"current_stage": "02"}

def node3(state: State):
    print("Running node3")
    return {"current_stage": "03"}

workflow = StateGraph(State)
workflow.add_node("01", node1)
workflow.add_node("02", node2)
workflow.add_node("03", node3)

def router(state: State):
    return state["current_stage"]

workflow.add_conditional_edges(START, router, {"01": "01", "02": "02", "03": "03"})
workflow.add_edge("01", END)
workflow.add_edge("02", END)
workflow.add_edge("03", END)

checkpointer = MemorySaver()
app = workflow.compile(checkpointer=checkpointer)

config = {"configurable": {"thread_id": "1"}}
print("--- Invoke 1 ---")
print(app.invoke({"current_stage": "01"}, config))

print("--- Invoke 2 ---")
print(app.invoke({"current_stage": "03"}, config))

