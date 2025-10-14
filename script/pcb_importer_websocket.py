"""
PCB Blender导入器

功能特性:
- 自动检查并安装websockets库
- 内置WebSocket注册表管理
- 支持OBJ和ZIP文件导入
- WebSocket服务器，接收远程文件传输
- 简化的单光源照明系统 (Blender 4.5)
- 正交/透视摄像头选择 (默认正交)
- 自动场景设置和优化
- 兼容多版本Blender
- 增强的金属质感材质处理
"""

import sys
import os
import subprocess
import importlib.util

def check_and_install_websockets():
    """检查并自动安装websockets库"""
    print("检查websockets库...")
    
    try:
        import websockets
        print("websockets库已安装")
        return True
    except ImportError:
        print("websockets库未安装，正在自动安装...")
        
        try:
            # 获取Blender的Python路径
            python_exe = sys.executable
            
            # 尝试安装websockets
            result = subprocess.run([
                python_exe, "-m", "pip", "install", "websockets==13.1"
            ], capture_output=True, text=True, check=True)
            
            print("websockets库安装成功")
            print(f"安装输出: {result.stdout}")
            
            import websockets
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"websockets库安装失败: {e}")
            print(f"错误输出: {e.stderr}")
            return False
        except Exception as e:
            print(f"安装过程中出现错误: {e}")
            return False

# 首先检查并安装websockets
if not check_and_install_websockets():
    print("无法安装websockets库，脚本退出")
    sys.exit(1)

try:
    import bpy
    import zipfile
    import tempfile
    import shutil
    from mathutils import Vector, Euler
    import mathutils
    import math
    from pathlib import Path
    from bpy_extras.io_utils import ImportHelper
    from bpy.props import StringProperty, BoolProperty, EnumProperty
    from bpy.types import Operator, Panel
    
    # WebSocket相关导入
    import asyncio
    import websockets
    import json
    import threading
    import base64
    from concurrent.futures import ThreadPoolExecutor
    import time
    from datetime import datetime, timedelta
    
except ImportError as e:
    print(f"导入错误: {e}")
    print("此脚本需要在Blender环境中运行")
    sys.exit(1)

class WebSocketRegistry:
    """WebSocket服务器注册表管理器"""
    
    def __init__(self, registry_file="websocket_registry.json"):
        self.registry_file = registry_file
        self.servers = {}
        self.load_registry()
    
    def load_registry(self):
        """从文件加载注册表"""
        try:
            if os.path.exists(self.registry_file):
                with open(self.registry_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.servers = data.get('servers', {})
                    print(f"加载注册表: {len(self.servers)} 个服务器")
            else:
                print("创建新的注册表")
                self.servers = {}
        except Exception as e:
            print(f"加载注册表失败: {e}")
            self.servers = {}
    
    def save_registry(self):
        """保存注册表到文件"""
        try:
            data = {
                'servers': self.servers,
                'last_updated': datetime.now().isoformat()
            }
            with open(self.registry_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"注册表已保存: {len(self.servers)} 个服务器")
        except Exception as e:
            print(f"保存注册表失败: {e}")
    
    def register_server(self, server_id, host, port, name="PCB WebSocket Server"):
        """注册服务器"""
        server_info = {
            'host': host,
            'port': port,
            'name': name,
            'registered_at': datetime.now().isoformat(),
            'last_heartbeat': datetime.now().isoformat(),
            'status': 'active'
        }
        self.servers[server_id] = server_info
        self.save_registry()
        print(f"服务器已注册: {server_id} ({host}:{port})")
    
    def unregister_server(self, server_id):
        """注销服务器"""
        if server_id in self.servers:
            del self.servers[server_id]
            self.save_registry()
            print(f"服务器已注销: {server_id}")
        else:
            print(f"服务器未找到: {server_id}")
    
    def update_heartbeat(self, server_id):
        """更新服务器心跳"""
        if server_id in self.servers:
            self.servers[server_id]['last_heartbeat'] = datetime.now().isoformat()
            self.servers[server_id]['status'] = 'active'
            # 每10次心跳保存一次，减少IO
            if hash(server_id) % 10 == 0:
                self.save_registry()
    
    def get_active_servers(self, timeout_minutes=5):
        """获取活跃的服务器列表"""
        active_servers = {}
        cutoff_time = datetime.now() - timedelta(minutes=timeout_minutes)
        
        for server_id, info in self.servers.items():
            try:
                last_heartbeat = datetime.fromisoformat(info['last_heartbeat'])
                if last_heartbeat > cutoff_time:
                    active_servers[server_id] = info
                else:
                    # 标记为非活跃但不删除
                    info['status'] = 'inactive'
            except Exception as e:
                print(f"解析心跳时间失败 {server_id}: {e}")
        
        return active_servers
    
    def cleanup_inactive_servers(self, timeout_hours=24):
        """清理长时间不活跃的服务器"""
        cutoff_time = datetime.now() - timedelta(hours=timeout_hours)
        to_remove = []
        
        for server_id, info in self.servers.items():
            try:
                last_heartbeat = datetime.fromisoformat(info['last_heartbeat'])
                if last_heartbeat < cutoff_time:
                    to_remove.append(server_id)
            except Exception as e:
                print(f"解析心跳时间失败 {server_id}: {e}")
                to_remove.append(server_id)
        

        for server_id in to_remove:
            del self.servers[server_id]
            print(f"清理非活跃服务器: {server_id}")
        
        if to_remove:
            self.save_registry()

class RegistryService:
    """注册表服务，管理心跳和清理"""
    
    def __init__(self, registry):
        self.registry = registry
        self.heartbeat_thread = None
        self.is_running = False
    
    def start_heartbeat(self, server_id, interval=30):
        """启动心跳线程"""
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            return
        
        self.is_running = True
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_worker,
            args=(server_id, interval),
            daemon=True
        )
        self.heartbeat_thread.start()
        print(f"心跳服务已启动: {server_id}")
    
    def stop_heartbeat(self):
        """停止心跳线程"""
        self.is_running = False
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=1)
        print("心跳服务已停止")
    
    def _heartbeat_worker(self, server_id, interval):
        """心跳工作线程"""
        while self.is_running:
            try:
                self.registry.update_heartbeat(server_id)
                time.sleep(interval)
            except Exception as e:
                print(f"心跳更新失败: {e}")
                time.sleep(interval)

class WebSocketPCBServer:
    """WebSocket PCB导入服务器"""
    
    def __init__(self, pcb_importer, host="0.0.0.0", port=8765):
        self.pcb_importer = pcb_importer
        self.host = host
        self.port = port
        self.server = None
        self.is_running = False
        self.clients = set()
        self.temp_files = {}
        
        # 初始化注册表和注册服务
        self.registry = WebSocketRegistry()
        self.registry_service = RegistryService(self.registry)
        self.server_id = f"blender_pcb_{host}_{port}_{int(time.time())}"
        
        print(f"初始化WebSocket服务器 {host}:{port}")
        print(f"服务器ID: {self.server_id}")
    
    async def register_client(self, websocket, path=None):
        """注册新客户端连接"""
        self.clients.add(websocket)
        
        try:
            # 安全获取客户端地址
            try:
                client_addr = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
            except:
                client_addr = "unknown"
            
            print(f"客户端连接: {client_addr}")
            
            await websocket.send(json.dumps({
                "type": "connection_established",
                "message": "成功连接到Blender PCB导入器"
            }))
            
            async for message in websocket:
                await self.handle_message(websocket, message)
                
        except websockets.exceptions.ConnectionClosed:
            print(f"客户端断开: {client_addr if 'client_addr' in locals() else 'unknown'}")
        except Exception as e:
            print(f"客户端处理错误: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.clients.discard(websocket)
    
    async def handle_message(self, websocket, message):
        """处理客户端消息"""
        try:
            data = json.loads(message)
            message_type = data.get('type')
            
            if message_type == 'file_upload':
                await self.handle_file_upload(websocket, data)
            elif message_type == 'ping':
                await websocket.send(json.dumps({"type": "pong"}))
            else:
                print(f"未知消息类型: {message_type}")
                
        except json.JSONDecodeError:
            await self.send_error(websocket, "无效的JSON消息")
        except Exception as e:
            await self.send_error(websocket, f"消息处理错误: {str(e)}")
    
    async def handle_file_upload(self, websocket, data):
        """处理文件上传"""
        try:
            filename = data.get('filename')
            file_size = data.get('size')
            file_data = data.get('data')
            
            if not all([filename, file_size, file_data]):
                await self.send_error(websocket, "文件数据不完整")
                return
            
            print(f"接收文件: {filename} ({file_size} bytes)")
            
            # 发送进度更新
            await websocket.send(json.dumps({
                "type": "upload_progress",
                "progress": 50,
                "status": "正在处理文件数据..."
            }))
            
            # 将数组数据转换为字节
            file_bytes = bytes(file_data)
            
            # 创建临时文件
            temp_dir = tempfile.mkdtemp(prefix="websocket_pcb_")
            temp_file_path = os.path.join(temp_dir, filename)
            
            with open(temp_file_path, 'wb') as f:
                f.write(file_bytes)
            
            # 存储临时文件信息
            client_id = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
            self.temp_files[client_id] = {
                'path': temp_file_path,
                'dir': temp_dir,
                'filename': filename
            }
            
            await websocket.send(json.dumps({
                "type": "upload_progress",
                "progress": 100,
                "status": "文件上传完成"
            }))
            
            await websocket.send(json.dumps({
                "type": "upload_complete",
                "message": "文件上传成功，开始导入..."
            }))
            
            # 在主线程中执行Blender操作
            def import_in_main_thread():
                try:
                    # 使用PCB导入器处理文件
                    success = self.pcb_importer.import_pcb_model(temp_file_path)
                    
                    if success:
                        # 发送导入完成消息
                        asyncio.run_coroutine_threadsafe(
                            websocket.send(json.dumps({
                                "type": "import_complete",
                                "message": "PCB导入完成，正在设置场景...",
                                "details": f"成功导入 {filename}"
                            })),
                            self.loop
                        )
                        print(f"PCB导入完成: {filename}")
                        
                        # 计算场景边界
                        bounds = self.pcb_importer.calculate_scene_bounds()
                        print(f"场景边界计算完成")
                        
                        # 设置世界环境
                        self.pcb_importer.setup_world_environment()
                        
                        # 设置照明
                        lighting_success = self.pcb_importer.setup_lighting(bounds)
                        
                        # 设置摄像头 (默认使用正交摄像头)
                        camera_success = self.pcb_importer.setup_camera(bounds, use_orthographic=True)
                        
                        # 添加交互控制
                        self.pcb_importer.add_interactive_controls()
                        
                        # 发送场景设置完成消息
                        asyncio.run_coroutine_threadsafe(
                            websocket.send(json.dumps({
                                "type": "scene_setup_complete",
                                "message": "场景设置完成，开始自动渲染...",
                                "details": {
                                    'objects_imported': len(self.pcb_importer.imported_objects),
                                    'lighting_setup': lighting_success,
                                    'camera_setup': bool(camera_success),
                                    'bounds': {
                                        'center': list(bounds['center']),
                                        'size': list(bounds['size']),
                                        'diagonal': bounds['diagonal']
                                    }
                                }
                            })),
                            self.loop
                        )
                        
                        # 自动进入着色渲染模式
                        print("自动进入着色渲染模式...")
                        self.pcb_importer.set_shading_mode()
                        
                        asyncio.run_coroutine_threadsafe(
                            websocket.send(json.dumps({
                                "type": "shading_mode_set",
                                "message": "已进入着色渲染模式，可以预览效果"
                            })),
                            self.loop
                        )
                        print("已进入着色渲染模式")
                            
                    else:
                        asyncio.run_coroutine_threadsafe(
                            self.send_error(websocket, "PCB导入失败"),
                            self.loop
                        )
                        
                except Exception as e:
                    error_msg = f"导入过程中出错: {str(e)}"
                    print(f"{error_msg}")
                    import traceback
                    traceback.print_exc()
                    asyncio.run_coroutine_threadsafe(
                        self.send_error(websocket, error_msg),
                        self.loop
                    )
                finally:
                    # 清理临时文件
                    try:
                        if os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir)
                        if client_id in self.temp_files:
                            del self.temp_files[client_id]
                    except Exception as e:
                        print(f"清理临时文件失败: {e}")
            
            # 使用Blender的定时器在主线程中执行
            bpy.app.timers.register(import_in_main_thread, first_interval=0.1)
            
        except Exception as e:
            await self.send_error(websocket, f"文件上传处理错误: {str(e)}")
    
    async def send_error(self, websocket, message):
        """发送错误消息"""
        try:
            await websocket.send(json.dumps({
                "type": "error",
                "message": message
            }))
        except Exception as e:
            print(f"发送错误消息失败: {e}")
    
    def start_server(self):
        """启动WebSocket服务器"""
        self.stop_server()  # 确保旧实例已停止
        
        if self.is_running:
            print("服务器已在运行")
            return
        
        def run_server():
            try:
                # 创建新的事件循环
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                
                # 启动WebSocket服务器
                async def create_server():
                    return await websockets.serve(
                        self.register_client,
                        self.host,
                        self.port,
                        max_size=100 * 1024 * 1024,  # 100MB max message size
                        family=0  # 允许IPv4和IPv6
                    )
                
                self.server = self.loop.run_until_complete(create_server())
                self.is_running = True
                
                # 注册服务器到注册表
                self.registry.register_server(
                    self.server_id, 
                    self.host, 
                    self.port, 
                    "Blender PCB WebSocket Server"
                )
                
                # 启动心跳服务
                self.registry_service.start_heartbeat(self.server_id)
                
                print(f"WebSocket服务器启动成功!")
                print(f"地址: ws://localhost:{self.port}")
                print(f"地址: ws://127.0.0.1:{self.port}")
                print(f"等待客户端连接...")
                
                # 运行事件循环
                self.loop.run_forever()
                
            except OSError as e:
                if "Address already in use" in str(e):
                    print(f"端口 {self.port} 已被占用，请尝试其他端口")
                else:
                    print(f"网络错误: {e}")
                self.is_running = False
            except Exception as e:
                print(f"服务器启动失败: {e}")
                print(f"错误类型: {type(e).__name__}")
                import traceback
                print(f"详细错误: {traceback.format_exc()}")
                self.is_running = False
            finally:
                # 确保清理资源
                if hasattr(self, 'loop') and self.loop:
                    try:
                        self.loop.close()
                    except:
                        pass
        
        # 在单独线程中运行服务器
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        
        # 给服务器一点时间启动
        import time
        time.sleep(0.5)
    
    def stop_server(self):
        """停止WebSocket服务器"""
        if not self.is_running:
            return
        
        try:
            # 停止心跳服务
            self.registry_service.stop_heartbeat()
            
            # 从注册表注销服务器
            self.registry.unregister_server(self.server_id)
            
            # 关闭所有客户端连接
            for client in self.clients.copy():
                try:
                    asyncio.run_coroutine_threadsafe(client.close(), self.loop)
                except:
                    pass
            self.clients.clear()
            
            # 关闭服务器
            if self.server:
                self.server.close()
                # 等待服务器完全关闭
                if hasattr(self.server, 'wait_closed'):
                    asyncio.run_coroutine_threadsafe(self.server.wait_closed(), self.loop)
            
            # 停止事件循环
            if hasattr(self, 'loop') and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
            
            # 等待线程结束
            if hasattr(self, 'server_thread') and self.server_thread.is_alive():
                self.server_thread.join(timeout=2)
            
            self.is_running = False
            print("WebSocket服务器已停止")
            
        except Exception as e:
            print(f"停止服务器时出错: {e}")
            import traceback
            traceback.print_exc()

class UnifiedPCBImporter:
    """统一PCB导入器 - 合并光照、摄像头和导入功能"""
    
    def __init__(self):
        self.pcb_object = None
        self.temp_dir = None
        self.imported_objects = []
        
        print("初始化统一PCB导入器")
    
    def clear_scene(self):
        """清理场景"""
        print("清理场景")
        
        try:
            # 确保在对象模式
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            
            # 选择所有对象并删除
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.delete(use_global=False)
            
            # 清理未使用的数据
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
            
            print("场景清理完成")
            
        except Exception as e:
            print(f"场景清理时出现警告: {e}")
    
    def setup_scene(self):
        """设置Blender场景"""
        print("设置Blender场景...")
        
        # 清除现有场景
        self.clear_scene()
        
        # 设置渲染引擎为Cycles (与importer.py一致)
        bpy.context.scene.render.engine = 'CYCLES'
        bpy.context.scene.cycles.samples = 128
        bpy.context.scene.cycles.use_denoising = True
        
        # 配置GPU渲染
        self.setup_gpu_rendering()
        
        # 设置分辨率
        bpy.context.scene.render.resolution_x = 1920
        bpy.context.scene.render.resolution_y = 1080
        bpy.context.scene.render.resolution_percentage = 100
        
        # 设置光照阈值
        bpy.context.scene.cycles.light_sampling_threshold = 0.01
        
        # 设置间接光照
        bpy.context.scene.cycles.max_bounces = 6
        bpy.context.scene.cycles.diffuse_bounces = 3
        bpy.context.scene.cycles.glossy_bounces = 3
        bpy.context.scene.cycles.transmission_bounces = 3
        
        # 设置曝光和色彩管理
        bpy.context.scene.view_settings.exposure = 0.0
        bpy.context.scene.view_settings.gamma = 1.0
        bpy.context.scene.view_settings.view_transform = 'Standard'
        bpy.context.scene.view_settings.look = 'Medium High Contrast'
        
        print("场景设置完成 (Cycles引擎 + GPU渲染)")
    
    def set_shading_mode(self):
        """设置渲染模式（而非材质预览模式）"""
        try:
            # 获取所有3D视图区域
            for area in bpy.context.screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            # 设置为渲染模式 (RENDERED)
                            space.shading.type = 'RENDERED'
                            print(f"✅ 3D视图已设置为渲染模式")
                            break
            
            # 确保使用Cycles渲染引擎
            if bpy.context.scene.render.engine != 'CYCLES':
                bpy.context.scene.render.engine = 'CYCLES'
                print("渲染引擎已设置为Cycles")
            
            return True
            
        except Exception as e:
            print(f"设置渲染模式失败: {e}")
            return False
    def setup_gpu_rendering(self):
        """配置GPU渲染"""
        print("配置GPU渲染...")
        
        try:
            # 获取Cycles渲染设置
            cycles_prefs = bpy.context.preferences.addons['cycles'].preferences
            
            # 尝试不同的GPU设备类型
            gpu_types = ['OPTIX', 'CUDA', 'OPENCL']
            gpu_found = False
            
            for gpu_type in gpu_types:
                try:
                    cycles_prefs.compute_device_type = gpu_type
                    cycles_prefs.get_devices()
                    
                    # 检查是否有可用的GPU设备
                    gpu_devices = [device for device in cycles_prefs.devices if device.type == gpu_type]
                    if gpu_devices:
                        # 启用所有可用的GPU设备
                        for device in gpu_devices:
                            device.use = True
                            print(f"启用GPU设备: {device.name} ({device.type})")
                        
                        # 设置场景使用GPU渲染
                        bpy.context.scene.cycles.device = 'GPU'
                        gpu_found = True
                        print(f"GPU渲染配置完成 ({gpu_type})")
                        break
                        
                except Exception as e:
                    print(f"{gpu_type} 不可用: {e}")
                    continue
            
            if not gpu_found:
                print("未找到可用的GPU设备，使用CPU渲染")
                bpy.context.scene.cycles.device = 'CPU'
            
        except Exception as e:
            print(f"GPU渲染配置失败，使用CPU渲染: {e}")
            bpy.context.scene.cycles.device = 'CPU'
    
    def extract_zip_file(self, zip_path):
        """解压ZIP文件并返回OBJ文件路径"""
        print(f"解压ZIP文件: {zip_path}")
        
        try:
            # 创建临时目录
            self.temp_dir = tempfile.mkdtemp(prefix="pcb_import_")
            
            # 解压文件
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(self.temp_dir)
            
            # 修改MTL文件以添加金属质感
            self.modify_mtl_files()
            
            # 查找OBJ文件
            obj_files = []
            for root, dirs, files in os.walk(self.temp_dir):
                for file in files:
                    if file.lower().endswith('.obj'):
                        obj_files.append(os.path.join(root, file))
            
            if not obj_files:
                print("ZIP文件中没有找到OBJ文件")
                return None
            
            # 选择最大的OBJ文件
            largest_obj = max(obj_files, key=os.path.getsize)
            print(f"找到OBJ文件: {os.path.basename(largest_obj)}")
            
            return largest_obj
            
        except Exception as e:
            print(f"解压ZIP文件失败: {e}")
            return None
    
    def modify_mtl_files(self):
        """修改临时目录中的MTL文件，为所有材质添加金属质感"""
        if not self.temp_dir:
            return
        
        print("为所有MTL材质添加金属质感...")
        
        try:
            # 查找所有MTL文件
            mtl_files = []
            for root, dirs, files in os.walk(self.temp_dir):
                for file in files:
                    if file.lower().endswith('.mtl'):
                        mtl_files.append(os.path.join(root, file))
            
            if not mtl_files:
                print("未找到MTL文件")
                return
            
            # 修改每个MTL文件
            for mtl_file in mtl_files:
                self._modify_single_mtl_file(mtl_file)
                
        except Exception as e:
            print(f"修改MTL文件失败: {e}")
    
    def _modify_single_mtl_file(self, mtl_file_path):
        """修改单个MTL文件，为所有材质添加金属质感"""
        try:
            # 读取MTL文件
            with open(mtl_file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # 修改所有材质，添加金属质感
            modified_lines = []
            current_material = None
            materials_modified = []
            
            for line in lines:
                stripped_line = line.strip()
                
                # 检测新材质定义
                if stripped_line.startswith('newmtl '):
                    current_material = stripped_line.split()[1]
                    # 预先检测材质是否为金属（基于名称和后续颜色分析）
                    self._current_material_is_metal = False
                    modified_lines.append(line)
                    
                # 修改材质属性以增加金属质感
                elif current_material and (stripped_line.startswith('Ka ') or 
                                         stripped_line.startswith('Kd ') or 
                                         stripped_line.startswith('Ks ') or
                                         stripped_line.startswith('Ns ') or
                                         stripped_line.startswith('Ni ') or
                                         stripped_line.startswith('d ') or
                                         stripped_line.startswith('Tr ') or
                                         stripped_line.startswith('illum ')):
                    
                    prefix = stripped_line.split()[0]
                    
                    if prefix == 'Ka':  # 环境光 - 强化金属反射效果
                        # 解析原始颜色值
                        parts = stripped_line.split()
                        if len(parts) >= 4:
                            try:
                                r, g, b = float(parts[1]), float(parts[2]), float(parts[3])
                                # 判断是否为金属材质（低饱和度，高亮度）
                                is_metal = self._is_metal_color(r, g, b)
                                # 设置当前材质的金属标识，供其他参数使用
                                self._current_material_is_metal = is_metal
                                
                                if is_metal:
                                    # 金属材质：大幅增强环境反射，创造强烈反光效果
                                    r = min(1.0, r * 1.8 + 0.3)  # 进一步增强环境反射
                                    g = min(1.0, g * 1.8 + 0.3)
                                    b = min(1.0, b * 1.8 + 0.3)
                                else:
                                    # 非金属材质：适度增强
                                    r = min(1.0, r * 1.2 + 0.08)  # 稍微增强非金属环境反射
                                    g = min(1.0, g * 1.2 + 0.08)
                                    b = min(1.0, b * 1.2 + 0.08)
                                
                                modified_lines.append(f'Ka {r:.3f} {g:.3f} {b:.3f}\n')
                            except ValueError:
                                modified_lines.append(line)
                        else:
                            modified_lines.append(line)
                            
                    elif prefix == 'Kd':  # 漫反射 - 根据材质类型调整
                        parts = stripped_line.split()
                        if len(parts) >= 4:
                            try:
                                r, g, b = float(parts[1]), float(parts[2]), float(parts[3])
                                is_metal = self._is_metal_color(r, g, b)
                                
                                if is_metal:
                                    # 金属材质：降低漫反射，增加镜面反射
                                    r = r * 0.8
                                    g = g * 0.8
                                    b = b * 0.8
                                
                                modified_lines.append(f'Kd {r:.3f} {g:.3f} {b:.3f}\n')
                            except ValueError:
                                modified_lines.append(line)
                        else:
                            modified_lines.append(line)
                         
                    elif prefix == 'Ks':  # 镜面反射 - 强化金属光泽
                        # 解析原始颜色值
                        parts = stripped_line.split()
                        if len(parts) >= 4:
                            try:
                                r, g, b = float(parts[1]), float(parts[2]), float(parts[3])
                                is_metal = self._is_metal_color(r, g, b)
                                
                                if is_metal:
                                    # 金属材质：大幅增强镜面反射，创造强烈反光效果
                                    r = min(1.0, r * 2.5 + 0.4)  # 进一步增强反光
                                    g = min(1.0, g * 2.5 + 0.4)
                                    b = min(1.0, b * 2.5 + 0.4)
                                else:
                                    # 非金属材质：适度增强镜面反射
                                    r = min(1.0, r * 1.5 + 0.15)  # 稍微增强非金属反光
                                    g = min(1.0, g * 1.5 + 0.15)
                                    b = min(1.0, b * 1.5 + 0.15)
                                
                                modified_lines.append(f'Ks {r:.3f} {g:.3f} {b:.3f}\n')
                            except ValueError:
                                modified_lines.append(line)
                        else:
                            modified_lines.append(line)
                             
                    elif prefix == 'Ns':  # 镜面指数 - 控制反光锐度
                        parts = stripped_line.split()
                        if len(parts) >= 2:
                            try:
                                ns_value = float(parts[1])
                                # 根据材质类型调整镜面指数
                                if hasattr(self, '_current_material_is_metal') and self._current_material_is_metal:
                                    # 金属材质：极高的镜面指数，创造锐利的反光
                                    ns_value = max(300.0, min(1500.0, ns_value * 4.0 + 200.0))  # 进一步增强锐利度
                                else:
                                    # 非金属材质：适度提高镜面指数
                                    ns_value = max(80.0, min(400.0, ns_value * 2.2 + 50.0))  # 稍微增强非金属锐利度
                                
                                modified_lines.append(f'Ns {ns_value:.1f}\n')
                            except ValueError:
                                modified_lines.append(line)
                        else:
                            modified_lines.append(line)
                             
                    elif prefix == 'Ni':  # 折射率 - 影响反射特性
                        parts = stripped_line.split()
                        if len(parts) >= 2:
                            try:
                                ni_value = float(parts[1])
                                # 根据材质类型调整折射率
                                if hasattr(self, '_current_material_is_metal') and self._current_material_is_metal:
                                    # 金属材质：高折射率，增强反射效果
                                    ni_value = max(2.0, min(5.0, ni_value * 1.8 + 1.2))
                                else:
                                    # 非金属材质：适度调整折射率
                                    ni_value = max(1.2, min(2.5, ni_value * 1.3 + 0.3))
                                
                                modified_lines.append(f'Ni {ni_value:.3f}\n')
                            except ValueError:
                                modified_lines.append(line)
                        else:
                            modified_lines.append(line)
                        
                    elif prefix == 'd' or prefix == 'Tr':  # 透明度 - 确保不透明
                        # 金属材质应该是不透明的
                        modified_lines.append('d 1.000\n')
                        
                    elif prefix == 'illum':  # 光照模型 - 使用适合金属的光照模型
                        # illum 2: 带高光的漫反射和环境光
                        modified_lines.append('illum 2\n')
                        
                    if current_material not in materials_modified:
                        materials_modified.append(current_material)
                        
                else:
                    modified_lines.append(line)
                    
                    # 记录当前材质的漫反射颜色，用于判断材质类型
                    if stripped_line.startswith('Kd '):
                        parts = stripped_line.split()
                        if len(parts) >= 4:
                            try:
                                self._current_kd_color = (float(parts[1]), float(parts[2]), float(parts[3]))
                            except ValueError:
                                pass
            
            # 写回修改后的内容
            with open(mtl_file_path, 'w', encoding='utf-8') as f:
                f.writelines(modified_lines)
            
            print(f"MTL文件修改完成: {os.path.basename(mtl_file_path)}")
            print(f"已为 {len(materials_modified)} 个材质添加金属质感")
            if materials_modified:
                print(f"修改的材质: {', '.join(materials_modified[:5])}{'...' if len(materials_modified) > 5 else ''}")
                
        except Exception as e:
            print(f"修改MTL文件失败 {os.path.basename(mtl_file_path)}: {e}")
    
    def _is_metal_color(self, r, g, b):
        """判断颜色是否为金属材质
        基于materials_summary.md中的逻辑：低饱和度且高亮度的颜色可能是金属
        增强版本：更宽松的金属检测条件，增强反光效果
        """
        # 计算HSV值
        max_val = max(r, g, b)
        min_val = min(r, g, b)
        
        # 计算亮度 (Value)
        v = max_val
        
        # 计算饱和度 (Saturation)
        if max_val == 0:
            s = 0
        else:
            s = (max_val - min_val) / max_val
        
        # 增强版金属材质判断：更宽松的条件以增强更多材质的反光效果
        # 原条件：s < 0.1 and v > 0.25
        # 新条件：s < 0.2 and v > 0.15 (更多材质被识别为金属)
        return s < 0.2 and v > 0.15

    def import_pcb_model(self, file_path):
        """导入PCB模型 (支持OBJ和ZIP)"""
        print(f"导入PCB模型: {file_path}")
        
        # 在导入前清空场景
        self.clear_scene()
        
        if not os.path.exists(file_path):
            print(f"文件不存在: {file_path}")
            return False
        
        # 确定文件类型
        file_ext = os.path.splitext(file_path)[1].lower()
        obj_file = None
        
        if file_ext == '.zip':
            obj_file = self.extract_zip_file(file_path)
            if not obj_file:
                return False
        elif file_ext == '.obj':
            obj_file = file_path
        else:
            print(f"不支持的文件格式: {file_ext}")
            print("支持的格式: .obj, .zip")
            return False
        
        try:
            # 确保在对象模式
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            
            # 记录导入前的对象
            objects_before = set(bpy.data.objects)
            
            # 导入OBJ文件 - 使用不同版本的Blender兼容方式
            abs_path = os.path.abspath(obj_file)
            
            # 尝试不同的导入方法，适配不同版本的Blender
            import_success = False
            try:
                # 尝试新的操作符名称 (Blender 4.0+)
                bpy.ops.wm.obj_import(filepath=abs_path)
                import_success = True
                print("使用 Blender 4.0+ 导入方法")
            except AttributeError:
                try:
                    # 尝试旧的操作符名称 (Blender 3.x)
                    bpy.ops.import_scene.obj(filepath=abs_path)
                    import_success = True
                    print("使用 Blender 3.x 导入方法")
                except AttributeError:
                    try:
                        # 如果都不行，尝试另一个可能的名称
                        bpy.ops.import_mesh.obj(filepath=abs_path)
                        import_success = True
                        print("使用备用导入方法")
                    except AttributeError:
                        print("无法找到合适的OBJ导入操作符")
                        return False
            
            if not import_success:
                print("OBJ导入失败")
                return False
            
            # 获取导入的对象
            self.imported_objects = list(set(bpy.data.objects) - objects_before)
            
            if not self.imported_objects:
                print("没有导入任何对象")
                return False
            
            print(f"成功导入 {len(self.imported_objects)} 个对象")
            
            # 选择所有导入的对象
            bpy.ops.object.select_all(action='DESELECT')
            for obj in self.imported_objects:
                if obj.type == 'MESH':
                    obj.select_set(True)
            
            # 如果有多个对象，选择最大的作为主PCB对象
            mesh_objects = [obj for obj in self.imported_objects if obj.type == 'MESH']
            if mesh_objects:
                if len(mesh_objects) > 1:
                    # 按体积排序，选择最大的
                    largest_obj = max(mesh_objects, key=lambda obj: self._get_object_volume(obj))
                    self.pcb_object = largest_obj
                    print(f"选择最大对象作为主PCB: {self.pcb_object.name}")
                else:
                    self.pcb_object = mesh_objects[0]
                    
                # 设置活动对象
                bpy.context.view_layer.objects.active = self.pcb_object
                print(f"设置活动对象: {self.pcb_object.name}")
                
                # 修正PCB方向 - 让PCB平放到桌面而不是立在X轴上
                self.fix_pcb_orientation()
            
            return True
            
        except Exception as e:
            print(f"导入PCB模型失败: {e}")
            return False
    
    def fix_pcb_orientation(self):
        """修正PCB方向，让PCB平放到桌面而不是立在X轴上"""
        try:
            if not self.imported_objects:
                print("没有导入的对象需要修正方向")
                return False
            
            print("修正PCB方向...")
            
            # 选择所有导入的网格对象
            bpy.ops.object.select_all(action='DESELECT')
            mesh_objects = [obj for obj in self.imported_objects if obj.type == 'MESH']
            
            for obj in mesh_objects:
                obj.select_set(True)
            
            if mesh_objects:
                # 确保在对象模式
                if bpy.context.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                
                # 设置活动对象
                bpy.context.view_layer.objects.active = mesh_objects[0]
                
                # 设置旋转模式为欧拉角并应用旋转
                for obj in mesh_objects:
                    obj.rotation_mode = 'XYZ'
                    # 绕X轴旋转0度使PCB平放（根据用户反馈调整角度）
                    obj.rotation_euler = (math.radians(0), 0, 0)
                    print(f"   • 旋转对象 {obj.name}: X轴0度")
                
                # 更新场景
                bpy.context.view_layer.update()
                
                # 应用旋转变换
                bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
                print("应用旋转变换")
                
                # 重新计算原点
                bpy.ops.object.origin_set(type='GEOMETRY_TO_ORIGIN')
                print("重新计算原点")
                
                # 计算所有对象的边界框并移动到地面
                self.move_to_ground()
                
                print("PCB方向修正完成 - 现在平放在桌面上")
                return True
            else:
                print("没有找到网格对象")
                return False
                
        except Exception as e:
            print(f"修正PCB方向失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def move_to_ground(self):
        """将PCB移动到地面（Z=0平面）"""
        try:
            mesh_objects = [obj for obj in self.imported_objects if obj.type == 'MESH']
            if not mesh_objects:
                return
            
            # 计算所有对象的边界框
            min_coords = Vector((float('inf'), float('inf'), float('inf')))
            max_coords = Vector((float('-inf'), float('-inf'), float('-inf')))
            
            # 更新场景后重新计算边界框
            bpy.context.view_layer.update()
            
            for obj in mesh_objects:
                # 使用世界坐标系下的边界框
                bbox = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
                for corner in bbox:
                    min_coords.x = min(min_coords.x, corner.x)
                    min_coords.y = min(min_coords.y, corner.y)
                    min_coords.z = min(min_coords.z, corner.z)
                    max_coords.x = max(max_coords.x, corner.x)
                    max_coords.y = max(max_coords.y, corner.y)
                    max_coords.z = max(max_coords.z, corner.z)
            
            # 计算中心点
            center = (min_coords + max_coords) / 2
            
            print(f"边界框: min={min_coords}, max={max_coords}")
            print(f"中心点: {center}")
            
            # 移动所有对象到中心，并确保底部接触地面
            for obj in mesh_objects:
                obj.location.x -= center.x  # X轴居中
                obj.location.y -= center.y  # Y轴居中
                obj.location.z -= min_coords.z  # 将底部放在Z=0平面上
                print(f"移动对象 {obj.name} 到地面")
            
            print("PCB已移动到地面并居中")
            
        except Exception as e:
            print(f"移动PCB到地面失败: {e}")
            import traceback
            traceback.print_exc()
        """计算对象的近似体积"""
        if obj.type != 'MESH':
            return 0
        
        try:
            # 获取对象的边界框
            bbox = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
            
            # 计算边界框的尺寸
            min_x = min(v.x for v in bbox)
            max_x = max(v.x for v in bbox)
            min_y = min(v.y for v in bbox)
            max_y = max(v.y for v in bbox)
            min_z = min(v.z for v in bbox)
            max_z = max(v.z for v in bbox)
            
            # 计算体积
            volume = (max_x - min_x) * (max_y - min_y) * (max_z - min_z)
            return volume
            
        except Exception:
            return 0
    
    def calculate_scene_bounds(self):
        """计算场景边界框"""
        mesh_objects = [obj for obj in self.imported_objects if obj.type == 'MESH']
        
        if not mesh_objects:
            return {
                'center': mathutils.Vector((0, 0, 0)),
                'size': mathutils.Vector((10, 10, 5)),
                'min': mathutils.Vector((-5, -5, -2.5)),
                'max': mathutils.Vector((5, 5, 2.5)),
                'diagonal': 12.25
            }
        
        # 计算整体边界框
        min_coords = [float('inf')] * 3
        max_coords = [float('-inf')] * 3
        
        for obj in mesh_objects:
            # 获取对象的世界坐标边界框
            bbox_corners = [obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box]
            
            for corner in bbox_corners:
                for i in range(3):
                    min_coords[i] = min(min_coords[i], corner[i])
                    max_coords[i] = max(max_coords[i], corner[i])
        
        center = [(min_coords[i] + max_coords[i]) / 2 for i in range(3)]
        size = [max_coords[i] - min_coords[i] for i in range(3)]
        
        bounds = {
            'center': mathutils.Vector(center),
            'size': mathutils.Vector(size),
            'min': mathutils.Vector(min_coords),
            'max': mathutils.Vector(max_coords),
            'diagonal': mathutils.Vector(size).length
        }
        
        print(f"场景边界: 中心={center}, 大小={size}, 对角线长度={bounds['diagonal']:.2f}")
        return bounds
    
    def setup_world_environment(self):
        """设置世界环境"""
        world = bpy.context.scene.world
        
        # 确保世界有材质节点
        if not world.use_nodes:
            world.use_nodes = True
        
        # 获取世界材质节点
        nodes = world.node_tree.nodes
        links = world.node_tree.links
        
        # 清除现有节点
        nodes.clear()
        
        # 添加背景节点
        bg_node = nodes.new(type='ShaderNodeBackground')
        bg_node.inputs['Color'].default_value = (0.05, 0.05, 0.1, 1.0)  # 深蓝色背景
        bg_node.inputs['Strength'].default_value = 0.1  # 低强度环境光
        
        # 添加输出节点
        output_node = nodes.new(type='ShaderNodeOutputWorld')
        
        # 连接节点
        links.new(bg_node.outputs['Background'], output_node.inputs['Surface'])
        
        print("世界环境设置完成 - 深蓝色背景，低强度环境光")
    
    def setup_lighting(self, bounds=None):
        """设置简化的单光源照明系统 (Blender 4.5)"""
        print("设置简化照明系统")
        
        try:
            # 删除现有光源
            bpy.ops.object.select_all(action='DESELECT')
            for obj in bpy.context.scene.objects:
                if obj.type == 'LIGHT':
                    obj.select_set(True)
            
            if bpy.context.selected_objects:
                bpy.ops.object.delete()
                print("   已删除现有光源")
            
            # 设置世界环境
            self.setup_world_environment()
            
            # 计算光源位置
            if bounds:
                center = bounds['center']
                size = max(bounds['size'])
                distance = size * 2.0  # 根据场景大小调整距离
            else:
                center = mathutils.Vector((0, 0, 0))
                distance = 20.0
            
            # 设置光源位置为倾斜角度，增强金属反光效果
            light_location = center + mathutils.Vector((distance * 0.8, -distance * 0.6, distance * 1.2))
            
            # 添加太阳光
            bpy.ops.object.light_add(type='SUN', location=light_location)
            sun_light = bpy.context.active_object
            sun_light.name = "Main_Sun_Light"
            
            # 获取光源数据
            light_data = sun_light.data
            
            # 设置Blender 4.5新的光照参数
            light_data.energy = 1.5  
            
            # 使用新的exposure控制 (Blender 4.5特性)
            if hasattr(light_data, 'exposure'):
                light_data.exposure = 2.0  # 曝光值，增强亮度
            
            # 设置色温 (Blender 4.5特性)
            if hasattr(light_data, 'temperature'):
                light_data.temperature = 5500  # 日光色温 (K)
            
            # 设置太阳光角度大小
            light_data.angle = math.radians(0.5)  # 0.5度，接近真实太阳
            
            # 设置光源旋转，让它指向场景中心，增强金属反光
            direction = center - light_location
            sun_light.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
            
            print(f"主太阳光创建完成:")
            print(f"   • 位置: {light_location}")
            print(f"   • 能量: {light_data.energy}")
            if hasattr(light_data, 'exposure'):
                print(f"   • 曝光: {light_data.exposure}")
            if hasattr(light_data, 'temperature'):
                print(f"   • 色温: {light_data.temperature}K")
            
            return True
            
        except Exception as e:
            print(f"设置照明失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def setup_camera(self, bounds=None, use_orthographic=True):
        """设置摄像头 (默认正交摄像头)"""
        print("设置摄像头...")
        
        try:
            # 删除现有摄像头
            bpy.ops.object.select_all(action='DESELECT')
            for obj in bpy.context.scene.objects:
                if obj.type == 'CAMERA':
                    obj.select_set(True)
            
            if bpy.context.selected_objects:
                bpy.ops.object.delete()
                print("   已删除现有摄像头")
            
            # 计算摄像头位置
            if bounds:
                center = bounds['center']
                size = max(bounds['size'])
                # 参考现有脚本的距离计算方式
                distance = size * 2.0  # 增加距离以获得更好的俯视效果
            else:
                center = mathutils.Vector((0, 0, 0))
                distance = 20.0
            
            # 将相机位置设置为倾斜角度，增强视觉效果和金属反光
            camera_location = center + mathutils.Vector((distance * 0.7, -distance * 0.7, distance * 0.8))
            
            # 添加摄像头
            bpy.ops.object.camera_add(location=camera_location)
            camera_obj = bpy.context.active_object
            camera_obj.name = "PCB_Camera"  # 使用与其他脚本一致的命名
            
            # 获取摄像头数据
            camera_data = camera_obj.data
            
            # 设置摄像头类型 - 参考importer.py的正交摄像头选项
            if use_orthographic:
                camera_data.type = 'ORTHO'
                # 设置正交缩放，根据场景大小调整
                if bounds:
                    ortho_scale = max(bounds['size']) * 1.2
                else:
                    ortho_scale = 20.0
                camera_data.ortho_scale = ortho_scale
                print(f"   正交摄像头 - 缩放: {ortho_scale:.2f}")
            else:
                camera_data.type = 'PERSP'
                camera_data.lens = 50.0  # 50mm镜头 (标准视角)
                print(f"   透视摄像头 - 焦距: {camera_data.lens}mm")
            
            # 设置剪切距离
            camera_data.clip_start = 0.1
            camera_data.clip_end = 1000.0
            
            # 设置景深 (默认关闭)
            camera_data.dof.use_dof = False
            
            # 计算摄像头朝向 - 设置为倾斜角度看向模型中心
            target = center
            camera_loc = camera_location
            direction = target - camera_loc
            camera_obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
            
            # 设置为活动摄像头
            bpy.context.scene.camera = camera_obj
            
            # 设置视图为摄像头视角
            for area in bpy.context.screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            # 设置为摄像头视图
                            space.region_3d.view_perspective = 'CAMERA'
                            break
                    break
            
            print(f"✅ 摄像头创建完成:")
            print(f"   • 名称: {camera_obj.name}")
            print(f"   • 位置: {camera_location}")
            print(f"   • 类型: {'正交' if use_orthographic else '透视'}")
            
            return camera_obj
            
        except Exception as e:
            print(f"设置摄像头失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def add_interactive_controls(self):
        """添加交互控制"""
        print("添加交互控制...")
        
        try:
            # 添加一个空对象作为控制器
            bpy.ops.object.empty_add(location=(0, 0, 0))
            controller = bpy.context.active_object
            controller.name = "PCB_Controller"
            controller.empty_display_type = 'ARROWS'
            controller.empty_display_size = 2.0
            
            # 将PCB对象设为控制器的子对象
            if self.pcb_object:
                self.pcb_object.parent = controller
            
            print("交互控制添加完成")
            return True
            
        except Exception as e:
            print(f"添加交互控制失败: {e}")
            return False
    
    def start_render(self):
        """开始渲染"""
        print("开始渲染...")
        
        try:
            # 确保使用Cycles引擎和GPU
            bpy.context.scene.render.engine = 'CYCLES'
            bpy.context.scene.cycles.device = 'GPU'
            
            # 设置输出路径
            output_dir = tempfile.mkdtemp(prefix="pcb_render_")
            output_path = os.path.join(output_dir, "pcb_render.png")
            bpy.context.scene.render.filepath = output_path
            
            # 开始渲染
            bpy.ops.render.render(write_still=True)
            
            print(f"渲染完成: {output_path}")
            return output_path
            
        except Exception as e:
            print(f"渲染失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def cleanup_temp_files(self):
        """清理临时文件"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
                print("临时文件清理完成")
            except Exception as e:
                print(f"清理临时文件失败: {e}")
    
    def print_import_report(self):
        """打印导入报告"""
        print("\n" + "="*60)
        print("PCB导入报告")
        print("="*60)
        
        if self.imported_objects:
            print(f"成功导入 {len(self.imported_objects)} 个对象:")
            for i, obj in enumerate(self.imported_objects, 1):
                obj_type = "网格" if obj.type == 'MESH' else f"📦 {obj.type}"
                print(f"  {i:2d}. {obj_type} - {obj.name}")
                
                if obj.type == 'MESH' and obj.data.materials:
                    print(f" 材质数量: {len(obj.data.materials)}")
        else:
            print("没有导入任何对象")
        
        if self.pcb_object:
            print(f"\n主PCB对象: {self.pcb_object.name}")
            
            # 计算对象信息
            mesh = self.pcb_object.data
            print(f" 顶点数: {len(mesh.vertices):,}")
            print(f" 面数: {len(mesh.polygons):,}")
            print(f" 材质数: {len(mesh.materials)}")
            
            # 计算边界框
            min_coord, max_coord = self.calculate_scene_bounds()
            size = max_coord - min_coord
            print(f" 尺寸: {size.x:.2f} × {size.y:.2f} × {size.z:.2f}")
        
        print("\n提示:")
        print("   • 使用鼠标中键旋转视图")
        print("   • 使用滚轮缩放")
        print("   • 按数字键盘1/3/7切换视图")
        print("   • 按Z键切换着色模式")
        print("="*60)
    
    def run(self, file_path=None, camera_type='ORTHO'):
        """运行统一导入流程"""
        print("开始统一PCB导入流程")
        print("="*50)
        
        try:
            # 1. 设置场景
            self.setup_scene()
            
            # 2. 导入PCB模型
            if file_path:
                success = self.import_pcb_model(file_path)
                if not success:
                    print("PCB导入失败")
                    return False
            
            # 3. 计算场景边界
            bounds = self.calculate_scene_bounds()
            print(f"场景边界: {bounds['min']} 到 {bounds['max']}")
            
            # 4. 设置世界环境
            self.setup_world_environment()
            
            # 5. 设置照明
            self.setup_lighting(bounds)
            
            # 6. 设置摄像头
            use_ortho = camera_type == 'ORTHO'
            self.setup_camera(bounds, use_ortho)
            
            # 7. 添加交互控制
            self.add_interactive_controls()
            
            # 8. 清理临时文件
            self.cleanup_temp_files()
            
            # 9. 打印报告
            self.print_import_report()
            
            print("\n统一PCB导入完成!")
            return True
            
        except Exception as e:
            print(f"导入过程中出现错误: {e}")
            self.cleanup_temp_files()
            return False

# Blender操作符和面板
class IMPORT_OT_pcb_websocket_server(Operator):
    """启动PCB WebSocket服务器"""
    bl_idname = "import_scene.pcb_websocket_server"
    bl_label = "启动PCB WebSocket服务器"
    bl_description = "启动WebSocket服务器，接收远程PCB文件"
    
    def execute(self, context):
        global websocket_server, pcb_importer
        
        if not hasattr(bpy.types.Scene, 'websocket_server') or not bpy.types.Scene.websocket_server:
            # 创建PCB导入器和WebSocket服务器
            pcb_importer = UnifiedPCBImporter()
            websocket_server = WebSocketPCBServer(pcb_importer)
            
            # 启动服务器
            websocket_server.start_server()
            bpy.types.Scene.websocket_server = websocket_server
            
            self.report({'INFO'}, "WebSocket服务器已启动 (ws://localhost:8765)")
        else:
            self.report({'WARNING'}, "WebSocket服务器已在运行")
        
        return {'FINISHED'}

class IMPORT_OT_pcb_websocket_stop(Operator):
    """停止PCB WebSocket服务器"""
    bl_idname = "import_scene.pcb_websocket_stop"
    bl_label = "停止PCB WebSocket服务器"
    bl_description = "停止WebSocket服务器"
    
    def execute(self, context):
        if hasattr(bpy.types.Scene, 'websocket_server') and bpy.types.Scene.websocket_server:
            bpy.types.Scene.websocket_server.stop_server()
            bpy.types.Scene.websocket_server = None
            self.report({'INFO'}, "WebSocket服务器已停止")
        else:
            self.report({'WARNING'}, "WebSocket服务器未运行")
        
        return {'FINISHED'}

class IMPORT_OT_pcb_file_browser(Operator, ImportHelper):
    """PCB文件浏览器"""
    bl_idname = "import_scene.pcb_file_browser"
    bl_label = "导入PCB文件"
    bl_description = "选择并导入PCB文件 (OBJ或ZIP格式)"
    
    # 文件过滤器
    filename_ext = ".obj;.zip"
    filter_glob: StringProperty(
        default="*.obj;*.zip",
        options={'HIDDEN'},
        maxlen=255,
    )
    
    # 摄像头类型选择
    camera_type: EnumProperty(
        name="摄像头类型",
        description="选择摄像头类型",
        items=[
            ('ORTHO', "正交", "正交摄像头 (适合技术图纸)"),
            ('PERSP', "透视", "透视摄像头 (更自然的视角)"),
        ],
        default='ORTHO',
    )
    
    # 自动渲染选项
    auto_render: BoolProperty(
        name="自动渲染",
        description="导入完成后自动开始渲染",
        default=False,
    )
    
    def execute(self, context):
        # 创建导入器实例
        importer = UnifiedPCBImporter()
        
        # 运行导入流程
        success = importer.run(self.filepath, self.camera_type)
        
        if success:
            self.report({'INFO'}, f"PCB导入成功: {os.path.basename(self.filepath)}")
            
            # 如果启用了自动渲染
            if self.auto_render:
                render_path = importer.start_render()
                if render_path:
                    self.report({'INFO'}, f"渲染完成: {render_path}")
                else:
                    self.report({'WARNING'}, "渲染失败")
        else:
            self.report({'ERROR'}, "PCB导入失败")
        
        return {'FINISHED'}

class VIEW3D_PT_pcb_websocket_panel(Panel):
    """PCB WebSocket导入面板"""
    bl_label = "PCB WebSocket导入器"
    bl_idname = "VIEW3D_PT_pcb_websocket_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "PCB导入"
    
    def draw(self, context):
        layout = self.layout
        
        # WebSocket服务器控制
        box = layout.box()
        box.label(text="WebSocket服务器", icon='WORLD')
        
        row = box.row()
        row.operator("import_scene.pcb_websocket_server", icon='PLAY')
        row.operator("import_scene.pcb_websocket_stop", icon='PAUSE')
        
        # 服务器状态
        if hasattr(bpy.types.Scene, 'websocket_server') and bpy.types.Scene.websocket_server:
            if bpy.types.Scene.websocket_server.is_running:
                box.label(text="状态: 运行中", icon='CHECKMARK')
                box.label(text="地址: ws://localhost:8765")
            else:
                box.label(text="状态: 已停止", icon='X')
        else:
            box.label(text="状态: 未启动", icon='RADIOBUT_OFF')
        
        # 分隔线
        layout.separator()
        
        # 本地文件导入
        box = layout.box()
        box.label(text="本地文件导入", icon='FILEBROWSER')
        box.operator("import_scene.pcb_file_browser", icon='IMPORT')
        
        # 渲染控制
        box = layout.box()
        box.label(text="渲染控制", icon='RENDER_STILL')
        box.operator("render.render", text="开始渲染", icon='RENDER_STILL')
        
        # 使用说明
        layout.separator()
        box = layout.box()
        box.label(text="使用说明", icon='INFO')
        box.label(text="1. 启动WebSocket服务器")
        box.label(text="2. 打开HTML客户端页面")
        box.label(text="3. 连接并上传PCB文件")
        box.label(text="4. 可选择自动渲染")
        box.label(text="5. 支持GPU加速渲染")

# 注册和注销函数
def register():
    """注册Blender操作符和面板"""
    bpy.utils.register_class(IMPORT_OT_pcb_websocket_server)
    bpy.utils.register_class(IMPORT_OT_pcb_websocket_stop)
    bpy.utils.register_class(IMPORT_OT_pcb_file_browser)
    bpy.utils.register_class(VIEW3D_PT_pcb_websocket_panel)
    
    # 初始化场景属性
    bpy.types.Scene.websocket_server = None
    
    print("PCB WebSocket导入器已注册")

def unregister():
    """注销Blender操作符和面板"""
    # 停止WebSocket服务器
    if hasattr(bpy.types.Scene, 'websocket_server') and bpy.types.Scene.websocket_server:
        bpy.types.Scene.websocket_server.stop_server()
    
    bpy.utils.unregister_class(IMPORT_OT_pcb_websocket_server)
    bpy.utils.unregister_class(IMPORT_OT_pcb_websocket_stop)
    bpy.utils.unregister_class(IMPORT_OT_pcb_file_browser)
    bpy.utils.unregister_class(VIEW3D_PT_pcb_websocket_panel)
    
    # 清理场景属性
    if hasattr(bpy.types.Scene, 'websocket_server'):
        del bpy.types.Scene.websocket_server
    
    print("PCB WebSocket导入器已注销")

def main():
    """主函数 - 直接运行脚本时调用"""
    print("启动PCB WebSocket导入器")
    
    # 注册操作符和面板
    register()
    
    # 创建导入器实例
    global pcb_importer, websocket_server
    pcb_importer = UnifiedPCBImporter()
    websocket_server = WebSocketPCBServer(pcb_importer)
    
    # 启动WebSocket服务器
    websocket_server.start_server()
    bpy.types.Scene.websocket_server = websocket_server
    
    print("PCB WebSocket导入器启动完成!")
    print("现在可以使用HTML客户端连接并上传PCB文件")

# 全局变量
pcb_importer = None
websocket_server = None

if __name__ == "__main__":
    main()
