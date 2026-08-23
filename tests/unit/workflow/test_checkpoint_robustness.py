from sw_lib.workflow.checkpoint import FileCheckpointSaver
from sw_lib.core.config import TASKS

def test_file_checkpoint_saver_put_and_get(dummy_task):
    """验证基本的存取功能。"""
    saver = FileCheckpointSaver()
    config = {"configurable": {"thread_id": dummy_task}}
    checkpoint = {"id": "cp1", "v": 1, "channel_values": {}}
    metadata = {"source": "test"}
    
    # 存
    saver.put(config, checkpoint, metadata, {})
    
    # 取
    tup = saver.get_tuple(config)
    assert tup is not None
    assert tup.checkpoint["id"] == "cp1"
    assert tup.metadata["source"] == "test"

def test_file_checkpoint_saver_list(dummy_task):
    """验证列表查询功能。"""
    saver = FileCheckpointSaver()
    config = {"configurable": {"thread_id": dummy_task}}
    
    for i in range(5):
        checkpoint = {"id": f"cp{i}", "v": i}
        saver.put(config, checkpoint, {"idx": i}, {})
        
    items = list(saver.list(config))
    assert len(items) == 5
    # 应该按 ID 降序排列 (如果是 reverse=True)
    assert items[0].checkpoint["id"] == "cp4"

def test_file_checkpoint_saver_corruption_robustness(dummy_task):
    """模拟文件损坏情况下的鲁棒性。"""
    saver = FileCheckpointSaver()
    config = {"configurable": {"thread_id": dummy_task}}
    
    # 1. 存一个正常的
    saver.put(config, {"id": "ok1", "v": 10}, {"m": 1}, {})
    
    # 2. 手动破坏最新的文件
    cp_dir = TASKS / dummy_task / ".checkpoints"
    latest_file = cp_dir / "ok1.pkl"
    with open(latest_file, "wb") as f:
        f.write(b"NOT A PICKLE")
        
    # 3. 获取时应处理异常并返回 None (或优雅降级)
    tup = saver.get_tuple(config)
    assert tup is None

def test_file_checkpoint_saver_concurrent_writes(dummy_task):
    """并发写入压力测试。"""
    import threading
    saver = FileCheckpointSaver()
    config = {"configurable": {"thread_id": dummy_task}}
    
    def run_writes(start_idx, count):
        for i in range(start_idx, start_idx + count):
            saver.put(config, {"id": f"batch_{i}", "v": i}, {"idx": i}, {})
            
    threads = []
    for i in range(5):
        t = threading.Thread(target=run_writes, args=(i*20, 20))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    items = list(saver.list(config))
    assert len(items) == 100
