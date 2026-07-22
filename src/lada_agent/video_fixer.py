#!/usr/bin/env python3
"""
视频修复工具
用于修复有问题的视频文件，特别是时间戳问题
"""

import json
import os
import subprocess
import sys
from pathlib import Path


class VideoFixer:
    def __init__(self):
        self.supported_formats = [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"]

    def check_video_info(self, video_path: str) -> dict:
        """检查视频文件信息"""
        try:
            cmd = [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                video_path,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if result.returncode == 0:
                return json.loads(result.stdout)
            else:
                print(f"❌ 无法读取视频信息: {result.stderr}")
                return {}
        except Exception as e:
            print(f"❌ 检查视频信息失败: {e}")
            return {}

    def fix_video_timestamps(self, input_path: str, output_path: str) -> bool:
        """修复视频时间戳问题"""
        try:
            print(f"🔧 正在修复视频时间戳: {os.path.basename(input_path)}")

            # 使用FFmpeg修复时间戳
            cmd = [
                "ffmpeg",
                "-i",
                input_path,
                "-c",
                "copy",  # 复制流，不重新编码
                "-avoid_negative_ts",
                "make_zero",  # 修复负时间戳
                "-fflags",
                "+genpts",  # 重新生成时间戳
                "-y",  # 覆盖输出文件
                output_path,
            ]

            print(f"📋 执行命令: {' '.join(cmd)}")

            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")

            if result.returncode == 0:
                print(f"✅ 时间戳修复成功: {os.path.basename(output_path)}")
                return True
            else:
                print(f"❌ 时间戳修复失败: {result.stderr}")
                return False

        except Exception as e:
            print(f"❌ 修复过程出错: {e}")
            return False

    def reencode_video(self, input_path: str, output_path: str) -> bool:
        """重新编码视频（更彻底的修复）"""
        try:
            print(f"🔄 正在重新编码视频: {os.path.basename(input_path)}")

            cmd = [
                "ffmpeg",
                "-i",
                input_path,
                "-c:v",
                "libx264",  # 使用H.264编码
                "-c:a",
                "aac",  # 使用AAC音频编码
                "-preset",
                "fast",  # 编码速度
                "-crf",
                "23",  # 质量设置
                "-avoid_negative_ts",
                "make_zero",
                "-fflags",
                "+genpts",
                "-y",
                output_path,
            ]

            print(f"📋 执行命令: {' '.join(cmd)}")

            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")

            if result.returncode == 0:
                print(f"✅ 重新编码成功: {os.path.basename(output_path)}")
                return True
            else:
                print(f"❌ 重新编码失败: {result.stderr}")
                return False

        except Exception as e:
            print(f"❌ 重新编码过程出错: {e}")
            return False

    def fix_video_file(self, input_path: str, output_dir: str = None) -> str | None:
        """修复单个视频文件"""
        if not os.path.exists(input_path):
            print(f"❌ 文件不存在: {input_path}")
            return None

        # 检查文件格式
        file_ext = Path(input_path).suffix.lower()
        if file_ext not in self.supported_formats:
            print(f"❌ 不支持的视频格式: {file_ext}")
            return None

        # 设置输出路径
        if output_dir is None:
            output_dir = os.path.dirname(input_path)

        input_name = Path(input_path).stem
        output_path = os.path.join(output_dir, f"{input_name}_fixed{file_ext}")

        print(f"🎬 开始修复视频: {os.path.basename(input_path)}")
        print(f"📁 输出路径: {output_path}")

        # 先尝试简单的时间戳修复
        if self.fix_video_timestamps(input_path, output_path):
            return output_path

        # 如果时间戳修复失败，尝试重新编码
        print("⚠️  时间戳修复失败，尝试重新编码...")
        if self.reencode_video(input_path, output_path):
            return output_path

        print(f"❌ 无法修复视频: {os.path.basename(input_path)}")
        return None

    def batch_fix_videos(self, input_dir: str, output_dir: str = None) -> list[str]:
        """批量修复视频文件"""
        if not os.path.exists(input_dir):
            print(f"❌ 目录不存在: {input_dir}")
            return []

        if output_dir is None:
            output_dir = os.path.join(input_dir, "fixed")

        os.makedirs(output_dir, exist_ok=True)

        fixed_files = []
        video_files = []

        # 查找所有视频文件
        for root, _dirs, files in os.walk(input_dir):
            for file in files:
                if Path(file).suffix.lower() in self.supported_formats:
                    video_files.append(os.path.join(root, file))

        print(f"🔍 找到 {len(video_files)} 个视频文件")

        for i, video_path in enumerate(video_files, 1):
            print(f"\n📹 处理 {i}/{len(video_files)}: {os.path.basename(video_path)}")

            fixed_path = self.fix_video_file(video_path, output_dir)
            if fixed_path:
                fixed_files.append(fixed_path)

        print(f"\n✅ 修复完成！成功修复 {len(fixed_files)} 个文件")
        return fixed_files


def main():
    """主函数"""
    print("🎬 视频修复工具启动")

    fixer = VideoFixer()

    if len(sys.argv) < 2:
        print("使用方法:")
        print("  python video_fixer.py <视频文件路径>")
        print("  python video_fixer.py <目录路径> [输出目录]")
        print("\n示例:")
        print("  python video_fixer.py video.mp4")
        print("  python video_fixer.py C:\\Videos C:\\Fixed")
        return

    input_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"📁 输入路径: {input_path}")

    if os.path.isfile(input_path):
        # 修复单个文件
        print("🔧 单文件修复模式")
        fixed_path = fixer.fix_video_file(input_path, output_dir)
        if fixed_path:
            print(f"\n🎉 修复完成: {fixed_path}")
        else:
            print("\n💥 修复失败")
    elif os.path.isdir(input_path):
        # 批量修复
        print("🔧 批量修复模式")
        fixed_files = fixer.batch_fix_videos(input_path, output_dir)
        if fixed_files:
            print(f"\n🎉 批量修复完成，共修复 {len(fixed_files)} 个文件")
        else:
            print("\n💥 没有成功修复任何文件")
    else:
        print(f"❌ 路径不存在: {input_path}")


if __name__ == "__main__":
    main()
