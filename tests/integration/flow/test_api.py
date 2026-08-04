import urllib.request
import json
try:
    print("Testing connection to opencode API...")
    # Just a simple local server mock or real ping if possible. 
    # Since we use OpenCodeAgent, it spawns a local node server.
    from sw_lib.agents.opencode import OpenCodeAgent
    
    agent = OpenCodeAgent({}, "test-api", "01-brainstorming", 0, "opencode/deepseek-v4-flash-free")
    agent.start()
    print(f"Agent started at {agent._transport.server_url}")
    
    import time
    time.sleep(2)
    
    agent.send("Hello", is_system=True)
    print("Agent send complete.")
    agent.shutdown()
except Exception as e:
    import traceback
    traceback.print_exc()
