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
- 修复线程安全问题，使用Blender推荐的modal operator模式
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
    import queue
    
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

class MessageQueue:
    """线程安全的消息队列"""
    
    def __init__(self):
        self.queue = queue.Queue()
    
    def put(self, message):
        """添加消息到队列"""
        self.queue.put(message)
    
    def get_all(self):
        """获取所有消息"""
        messages = []
        while not self.queue.empty():
            try:
                messages.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return messages

class WebSocketPCBServer:
    """WebSocket PCB导入服务器 - 修复版本"""
    
    def __init__(self, pcb_importer, host="0.0.0.0", port=8765):
        self.pcb_importer = pcb_importer
        self.host = host
        self.port = port
        self.server = None
        self.is_running = False
        self.clients = set()
        self.temp_files = {}
        self.message_queue = MessageQueue()
        
        # 初始化注册表
        self.registry = WebSocketRegistry()
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
            
            # 将导入任务添加到消息队列，由主线程处理
            import_task = {
                'type': 'import_pcb',
                'file_path': temp_file_path,
                'filename': filename,
                'websocket_id': id(websocket),
                'client_id': client_id,
                'temp_dir': temp_dir
            }
            self.message_queue.put(import_task)
            
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
                self.cleanup_resources()
        
        # 在单独线程中运行服务器
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        
        # 给服务器一点时间启动
        time.sleep(0.5)
    
    def stop_server(self):
        """停止WebSocket服务器"""
        if not self.is_running:
            return
        
        try:
            # 从注册表注销服务器
            self.registry.unregister_server(self.server_id)
            
            # 停止事件循环
            if hasattr(self, 'loop') and self.loop and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self.loop.stop)
            
            self.is_running = False
            print("WebSocket服务器已停止")
            
        except Exception as e:
            print(f"停止服务器时出错: {e}")
        finally:
            self.cleanup_resources()
    
    def cleanup_resources(self):
        """完善的资源清理"""
        try:
            # 清理所有临时文件
            for temp_info in self.temp_files.values():
                try:
                    if os.path.exists(temp_info['dir']):
                        shutil.rmtree(temp_info['dir'])
                except Exception as e:
                    print(f"清理临时文件失败: {e}")
            
            self.temp_files.clear()
            
            # 清理事件循环
            if hasattr(self, 'loop') and self.loop and not self.loop.is_closed():
                try:
                    self.loop.close()
                except Exception as e:
                    print(f"关闭事件循环失败: {e}")
                    
        except Exception as e:
            print(f"资源清理失败: {e}")
    
    def process_message_queue(self):
        """处理消息队列中的任务（在主线程中调用）"""
        messages = self.message_queue.get_all()
        
        for message in messages:
            try:
                if message['type'] == 'import_pcb':
                    self.process_import_task(message)
            except Exception as e:
                print(f"处理消息失败: {e}")
                import traceback
                traceback.print_exc()
    
    def process_import_task(self, task):
        """处理PCB导入任务（在主线程中执行）"""
        try:
            file_path = task['file_path']
            filename = task['filename']
            temp_dir = task['temp_dir']
            
            print(f"开始导入PCB: {filename}")
            
            # 使用PCB导入器处理文件
            success = self.pcb_importer.import_pcb_model(file_path)
            
            if success:
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
                
                # 自动进入着色渲染模式
                print("自动进入着色渲染模式...")
                self.pcb_importer.set_shading_mode()
                print("已进入着色渲染模式")
                
            else:
                print("PCB导入失败")
                
        except Exception as e:
            error_msg = f"导入过程中出错: {str(e)}"
            print(f"{error_msg}")
            import traceback
            traceback.print_exc()
        finally:
            # 清理临时文件
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                # 从临时文件字典中移除
                client_id = task.get('client_id')
                if client_id and client_id in self.temp_files:
                    del self.temp_files[client_id]
            except Exception as e:
                print(f"清理临时文件失败: {e}")

# Modal Operator for WebSocket Server Management
class MESH_OT_websocket_pcb_server(bpy.types.Operator):
    """WebSocket PCB服务器 Modal Operator"""
    bl_idname = "mesh.websocket_pcb_server"
    bl_label = "WebSocket PCB Server"
    bl_description = "启动WebSocket PCB导入服务器"
    
    _timer = None
    _server = None
    
    def modal(self, context, event):
        """Modal事件处理"""
        if event.type == 'TIMER':
            # 处理WebSocket消息队列
            if self._server:
                self._server.process_message_queue()
            return {'PASS_THROUGH'}
        
        elif event.type == 'ESC':
            # ESC键停止服务器
            self.cancel(context)
            return {'CANCELLED'}
        
        return {'PASS_THROUGH'}
    
    def execute(self, context):
        """执行操作"""
        try:
            # 创建PCB导入器实例
            pcb_importer = PCBImporter()
            
            # 创建WebSocket服务器
            self._server = WebSocketPCBServer(pcb_importer)
            
            # 启动WebSocket服务器
            self._server.start_server()
            
            # 注册timer事件
            wm = context.window_manager
            self._timer = wm.event_timer_add(0.1, window=context.window)
            wm.modal_handler_add(self)
            
            self.report({'INFO'}, "WebSocket PCB服务器已启动")
            return {'RUNNING_MODAL'}
            
        except Exception as e:
            self.report({'ERROR'}, f"启动服务器失败: {str(e)}")
            return {'CANCELLED'}
    
    def cancel(self, context):
        """取消操作"""
        try:
            # 移除timer
            if self._timer:
                wm = context.window_manager
                wm.event_timer_remove(self._timer)
                self._timer = None
            
            # 停止服务器
            if self._server:
                self._server.stop_server()
                self._server = None
            
            self.report({'INFO'}, "WebSocket PCB服务器已停止")
            
        except Exception as e:
            print(f"停止服务器时出错: {e}")

# PCB导入器类（简化版本，包含主要功能）
class PCBImporter:
    """PCB导入器"""
    
    def __init__(self):
        self.imported_objects = []
    
    def import_pcb_model(self, file_path):
        """导入PCB模型"""
        try:
            if file_path.lower().endswith('.zip'):
                return self.import_zip_file(file_path)
            elif file_path.lower().endswith('.obj'):
                return self.import_obj_file(file_path)
            else:
                print(f"不支持的文件格式: {file_path}")
                return False
        except Exception as e:
            print(f"导入PCB模型失败: {e}")
            return False
    
    def import_zip_file(self, zip_path):
        """导入ZIP文件"""
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                temp_dir = tempfile.mkdtemp(prefix="pcb_extract_")
                zip_ref.extractall(temp_dir)
                
                # 查找OBJ文件
                obj_files = []
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        if file.lower().endswith('.obj'):
                            obj_files.append(os.path.join(root, file))
                
                success = False
                for obj_file in obj_files:
                    if self.import_obj_file(obj_file):
                        success = True
                
                # 清理临时目录
                shutil.rmtree(temp_dir)
                return success
                
        except Exception as e:
            print(f"导入ZIP文件失败: {e}")
            return False
    
    def import_obj_file(self, obj_path):
        """导入OBJ文件"""
        try:
            # 使用Blender的OBJ导入器
            bpy.ops.wm.obj_import(filepath=obj_path)
            
            # 获取新导入的对象
            new_objects = [obj for obj in bpy.context.selected_objects]
            self.imported_objects.extend(new_objects)
            
            print(f"成功导入OBJ文件: {obj_path}")
            return True
            
        except Exception as e:
            print(f"导入OBJ文件失败: {e}")
            return False
    
    def calculate_scene_bounds(self):
        """计算场景边界"""
        if not self.imported_objects:
            return {
                'center': Vector((0, 0, 0)),
                'size': Vector((1, 1, 1)),
                'diagonal': 1.0
            }
        
        # 计算所有对象的边界框
        min_coords = Vector((float('inf'), float('inf'), float('inf')))
        max_coords = Vector((float('-inf'), float('-inf'), float('-inf')))
        
        for obj in self.imported_objects:
            if obj.type == 'MESH':
                bbox = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
                for corner in bbox:
                    min_coords.x = min(min_coords.x, corner.x)
                    min_coords.y = min(min_coords.y, corner.y)
                    min_coords.z = min(min_coords.z, corner.z)
                    max_coords.x = max(max_coords.x, corner.x)
                    max_coords.y = max(max_coords.y, corner.y)
                    max_coords.z = max(max_coords.z, corner.z)
        
        center = (min_coords + max_coords) / 2
        size = max_coords - min_coords
        diagonal = size.length
        
        return {
            'center': center,
            'size': size,
            'diagonal': diagonal
        }
    
    def setup_world_environment(self):
        """设置世界环境"""
        try:
            world = bpy.context.scene.world
            if world is None:
                world = bpy.data.worlds.new("World")
                bpy.context.scene.world = world
            
            # 启用节点
            world.use_nodes = True
            nodes = world.node_tree.nodes
            nodes.clear()
            
            # 添加背景节点
            bg_node = nodes.new(type='ShaderNodeBackground')
            bg_node.inputs[0].default_value = (0.1, 0.1, 0.1, 1.0)  # 深灰色背景
            bg_node.inputs[1].default_value = 1.0
            
            # 添加输出节点
            output_node = nodes.new(type='ShaderNodeOutputWorld')
            
            # 连接节点
            world.node_tree.links.new(bg_node.outputs[0], output_node.inputs[0])
            
        except Exception as e:
            print(f"设置世界环境失败: {e}")
    
    def setup_lighting(self, bounds):
        """设置照明"""
        try:
            # 清除现有灯光
            bpy.ops.object.select_all(action='DESELECT')
            for obj in bpy.context.scene.objects:
                if obj.type == 'LIGHT':
                    obj.select_set(True)
            bpy.ops.object.delete()
            
            # 添加主光源
            bpy.ops.object.light_add(type='SUN', location=(0, 0, bounds['diagonal']))
            sun_light = bpy.context.active_object
            sun_light.data.energy = 5.0
            sun_light.rotation_euler = (math.radians(45), 0, math.radians(45))
            
            return True
            
        except Exception as e:
            print(f"设置照明失败: {e}")
            return False
    
    def setup_camera(self, bounds, use_orthographic=True):
        """设置摄像头"""
        try:
            # 获取或创建摄像头
            camera = None
            for obj in bpy.context.scene.objects:
                if obj.type == 'CAMERA':
                    camera = obj
                    break
            
            if camera is None:
                bpy.ops.object.camera_add()
                camera = bpy.context.active_object
            
            # 设置摄像头位置
            distance = bounds['diagonal'] * 1.5
            camera.location = bounds['center'] + Vector((distance, -distance, distance))
            
            # 让摄像头看向场景中心
            direction = bounds['center'] - camera.location
            camera.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
            
            # 设置摄像头类型
            if use_orthographic:
                camera.data.type = 'ORTHO'
                camera.data.ortho_scale = bounds['diagonal'] * 1.2
            else:
                camera.data.type = 'PERSP'
                camera.data.lens = 50
            
            # 设置为活动摄像头
            bpy.context.scene.camera = camera
            
            return camera
            
        except Exception as e:
            print(f"设置摄像头失败: {e}")
            return None
    
    def add_interactive_controls(self):
        """添加交互控制"""
        try:
            # 这里可以添加一些交互控制逻辑
            # 例如设置视口导航等
            pass
        except Exception as e:
            print(f"添加交互控制失败: {e}")
    
    def set_shading_mode(self):
        """设置着色模式"""
        try:
            # 设置所有3D视口为材质预览模式
            for area in bpy.context.screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            space.shading.type = 'MATERIAL'
                            break
        except Exception as e:
            print(f"设置着色模式失败: {e}")

# UI面板
class VIEW3D_PT_websocket_pcb_panel(bpy.types.Panel):
    """WebSocket PCB导入面板"""
    bl_label = "WebSocket PCB导入器"
    bl_idname = "VIEW3D_PT_websocket_pcb_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "PCB导入"
    
    def draw(self, context):
        layout = self.layout
        
        # 检查服务器状态
        server_running = self.check_server_status()
        
        if server_running:
            # 服务器运行中的UI
            layout.label(text="✅ 服务器运行中", icon='CHECKMARK')
            layout.operator("mesh.websocket_pcb_server", text="重启WebSocket服务器")
            layout.separator()
            layout.label(text="服务器地址:")
            layout.label(text="  ws://localhost:8765")
            layout.label(text="  ws://127.0.0.1:8765")
            layout.separator()
            layout.label(text="支持格式: OBJ, ZIP")
        else:
            # 服务器未运行的UI
            layout.label(text="❌ 服务器未运行", icon='ERROR')
            layout.operator("mesh.websocket_pcb_server", text="启动WebSocket服务器")
            layout.separator()
            layout.label(text="启动后地址:")
            layout.label(text="  ws://localhost:8765")
            layout.separator()
            layout.label(text="支持格式: OBJ, ZIP")
    
    def check_server_status(self):
        """检查服务器是否在运行"""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('localhost', 8765))
            sock.close()
            return result == 0
        except:
            return False

# 注册类
classes = [
    MESH_OT_websocket_pcb_server,
    VIEW3D_PT_websocket_pcb_panel,
]

def register():
    """注册插件"""
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    """注销插件"""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

def auto_start_server():
    """自动启动WebSocket服务器"""
    try:
        # 检查是否已有服务器在运行
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('localhost', 8765))
        sock.close()
        
        if result == 0:
            print("WebSocket服务器已在运行 (端口8765)")
            return
        
        # 启动服务器
        bpy.ops.mesh.websocket_pcb_server()
        print("WebSocket服务器自动启动成功!")
        print("服务器地址: ws://localhost:8765")
        print("等待客户端连接...")
        
    except Exception as e:
        print(f"自动启动WebSocket服务器失败: {e}")
        print("请手动启动:")
        print("1. 在3D视图中按 N 键打开侧边栏")
        print("2. 找到 'PCB导入' 标签页")
        print("3. 点击 '启动WebSocket服务器' 按钮")

if __name__ == "__main__":
    register()
    print("PCB WebSocket导入器已加载")
    
    # 延迟自动启动服务器，确保Blender完全初始化
    def delayed_start():
        auto_start_server()
    
    bpy.app.timers.register(delayed_start, first_interval=1.0)
