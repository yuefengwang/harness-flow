import pickle
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Sequence, Tuple

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    SerializerProtocol,
)

from ..core.config import TASKS

class FileCheckpointSaver(BaseCheckpointSaver):
    """A file-based checkpoint saver for LangGraph.
    
    Stores checkpoints in workspace/tasks/{task_name}/.checkpoints/
    """
    
    def __init__(self, *, serde: Optional[SerializerProtocol] = None):
        super().__init__(serde=serde)
        self._lock = threading.Lock()

    def _get_checkpoint_dir(self, thread_id: str) -> Path:
        path = TASKS / thread_id / ".checkpoints"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = get_checkpoint_id(config)
        checkpoint_dir = self._get_checkpoint_dir(thread_id)
        
        if checkpoint_id:
            path = checkpoint_dir / f"{checkpoint_id}.pkl"
            if not path.exists():
                return None
        else:
            # Find latest
            files = sorted(checkpoint_dir.glob("*.pkl"))
            if not files:
                return None
            path = files[-1]
            checkpoint_id = path.stem

        with self._lock:
            try:
                with open(path, "rb") as f:
                    data = pickle.load(f)
                
                # data is expected to be a dict with:
                # checkpoint: dict (serialized)
                # metadata: dict (serialized)
                # parent_checkpoint_id: str
                
                return CheckpointTuple(
                    config={
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_id": checkpoint_id
                        }
                    },
                    checkpoint=self.serde.loads_typed(data["checkpoint"]),
                    metadata=self.serde.loads_typed(data["metadata"]),
                    parent_config=(
                        {
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_id": data["parent_checkpoint_id"]
                            }
                        }
                        if data.get("parent_checkpoint_id")
                        else None
                    ),
                    pending_writes=[] # Simplified for now
                )
            except Exception:
                return None

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        if not config:
            return
            
        thread_id = config["configurable"]["thread_id"]
        checkpoint_dir = self._get_checkpoint_dir(thread_id)
        
        files = sorted(checkpoint_dir.glob("*.pkl"), reverse=True)
        
        count = 0
        for path in files:
            if limit and count >= limit:
                break
            
            checkpoint_id = path.stem
            if before and checkpoint_id >= get_checkpoint_id(before):
                continue
                
            tup = self.get_tuple({
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_id
                }
            })
            if tup:
                # Apply filter on metadata
                if filter:
                    if not all(tup.metadata.get(k) == v for k, v in filter.items()):
                        continue
                yield tup
                count += 1

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any, # Ignored in simplified version
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = checkpoint["id"]
        checkpoint_dir = self._get_checkpoint_dir(thread_id)
        path = checkpoint_dir / f"{checkpoint_id}.pkl"
        
        data = {
            "checkpoint": self.serde.dumps_typed(checkpoint),
            "metadata": self.serde.dumps_typed(metadata),
            "parent_checkpoint_id": config["configurable"].get("checkpoint_id")
        }
        
        with self._lock:
            with open(path, "wb") as f:
                pickle.dump(data, f)
                
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        # Simplified: pending-writes persistence is not supported yet. The
        # parameters are unused on purpose — the signature is fixed by
        # BaseCheckpointSaver and must stay compatible with LangGraph.
        pass
