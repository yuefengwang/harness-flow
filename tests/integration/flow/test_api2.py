from sw_lib.agents.opencode import OpenCodeAgent
import time

def on_log(source, msg):
    print(f"[{source}] {msg}")

agent = OpenCodeAgent({"add_log": on_log}, "test-api2", "01-brainstorming", 0, "opencode/deepseek-v4-flash-free")
agent.start()
print(f"Agent started at {agent._transport.server_url}")

time.sleep(2)
agent.send("Hello", is_system=True)
print("Agent send complete.")
agent.shutdown()
