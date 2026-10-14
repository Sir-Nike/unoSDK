"""Apply the declared PyTorch budget before loading a model. Not hard isolation."""
import os


def configure_torch(torch, require_cuda=False):
    torch.set_num_threads(1)
    if not require_cuda:
        return "cpu"
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA verification requires CUDA; CPU fallback is forbidden")
    budget = int(os.environ.get("UNO_GPU_MEMORY_BYTES", "0"))
    if budget <= 0:
        raise RuntimeError("UNO_GPU_MEMORY_BYTES must be set by the organizer")
    total = torch.cuda.get_device_properties(0).total_memory
    if budget > total:
        raise RuntimeError("declared GPU budget exceeds visible device memory")
    torch.cuda.set_per_process_memory_fraction(budget / total, device=0)
    return "cuda:0"
