import time
import re
import sys
import select
import subprocess
import plotext as plt

def monitor_simaai_mem_chart(sample_interval_sec=5, max_samples=100):
    sizes = []

    def read_allocated_size():
        try:
            output = subprocess.check_output(['sudo', 'cat', '/dev/simaai-mem'], text=True)
            match = re.search(r"Total allocated size:\s+0x([0-9a-fA-F]+)", output)
            if match:
                size_bytes = int(match.group(1), 16)
                return size_bytes / (1024 * 1024)
        except Exception as e:
            print(f"Error reading /dev/simaai-mem: {e}")
        return None

    print("📈 Monitoring MLA memory usage... (Press 'Ctrl+C' to quit)")

    while True:
        # Check for quit key
        if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
            key = sys.stdin.read(1)
            if key.lower() == 'q':
                print("\n❌ Exiting memory monitor...")
                break

        size_mb = read_allocated_size()
        if size_mb is not None:
            sizes.append(size_mb)
            sizes = sizes[-max_samples:]

        if sizes:
            plt.clear_data()
            plt.clc()  
            plt.title("SIMA MLA Memory Usage (MB)")
            plt.xlabel("Seconds")
            plt.ylabel("Memory (MB)")
            plt.plot(sizes)
            plt.ylim(min(sizes) * 0.95, max(sizes) * 1.05)
            plt.show()
        
        time.sleep(sample_interval_sec)
