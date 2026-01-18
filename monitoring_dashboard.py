import json
import logging
import os
from datetime import datetime
from pathlib import Path
from io import BytesIO
import matplotlib.pyplot as plt
import pandas as pd

logger = logging.getLogger(__name__)

class LawMonitor:
    def __init__(self):
        self.data_dir = Path("./data/monitoring")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.data_dir / "history.json"
        self.history = self.load_history()
    
    def load_history(self):
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r') as f:
                    return json.load(f)
            except:
                return []
        return []
    
    def add_checkpoint(self, updates_count: int, total_laws: int):
        """Monitoring nuqtasini qo'shish"""
        self.history.append({
            "timestamp": datetime.now().isoformat(),
            "updates": updates_count,
            "total_laws": total_laws
        })
        
        # Faqat oxirgi 100 ta saqlash
        if len(self.history) > 100:
            self.history = self.history[-100:]
        
        with open(self.history_file, 'w') as f:
            json.dump(self.history, f)
    
    def generate_report_image(self):
        """Hisobot rasmini yaratish"""
        if not self.history or len(self.history) < 1:
            return None
        
        try:
            df = pd.DataFrame(self.history)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            
            # Grafik yaratish
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
            
            # Yangilanishlar grafigi
            ax1.plot(df.index, df['updates'], marker='o', color='blue', linestyle='-', linewidth=2)
            ax1.set_title('Qonun Yangilanishlari (Dinamika)')
            ax1.set_ylabel('Yangilanganlar soni')
            ax1.grid(True, alpha=0.3)
            
            # Jami qonunlar
            ax2.plot(df.index, df['total_laws'], marker='s', color='green', linestyle='--', linewidth=2)
            ax2.set_title('Bazadagi Jami Qonunlar')
            ax2.set_ylabel('Soni')
            ax2.grid(True, alpha=0.3)
            
            plt.tight_layout()
            
            # Rasmni saqlash
            img_buffer = BytesIO()
            plt.savefig(img_buffer, format='png', dpi=100)
            img_buffer.seek(0)
            plt.close()
            
            return img_buffer
        except Exception as e:
            logger.error(f"❌ Grafik yaratishda xatolik: {e}")
            return None
