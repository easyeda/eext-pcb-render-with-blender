/**
 * PCB到Blender WebSocket渲染系统
 *
 * 本扩展实现了将PCB 3D模型文件通过WebSocket发送到Blender进行高质量渲染的功能
 * 
 * 主要功能：
 * 1. 检查与Blender WebSocket服务器的连接
 * 2. 获取当前PCB的3D模型文件
 * 3. 发送文件到Blender进行渲染
 * 4. 显示连接和渲染状态
 */
import * as extensionConfig from '../extension.json';

// WebSocket连接配置
const BLENDER_WEBSOCKET_ID = 'blender-pcb-renderer';
const DEFAULT_BLENDER_ADDRESS = 'ws://localhost:8765';

// 连接状态管理
let isConnectedToBlender = false;
let isRendering = false;

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function activate(status?: 'onStartupFinished', arg?: string): void {
	// 扩展激活时的初始化逻辑
}

export function about(): void {
	eda.sys_Dialog.showInformationMessage(
		eda.sys_I18n.text('PCB Blender 渲染工具 v', undefined, undefined, extensionConfig.version),
		eda.sys_I18n.text('About'),
	);
}

/**
 * 异步连接到Blender WebSocket服务器
 */
async function connectToBlenderAsync(): Promise<void> {
	return new Promise((resolve, reject) => {
		try {
			// 注册WebSocket连接
			eda.sys_WebSocket.register(
				BLENDER_WEBSOCKET_ID,
				DEFAULT_BLENDER_ADDRESS,
				handleBlenderMessage,
				() => {
					isConnectedToBlender = true;
					eda.sys_Message.showToastMessage(
						eda.sys_I18n.text('成功连接到Blender服务器!'),
						'success'
					);
					resolve();
				}
			);
			
			// 设置超时
			setTimeout(() => {
				if (!isConnectedToBlender) {
					reject(new Error('连接超时'));
				}
			}, 5000);
			
		} catch (error) {
			reject(error);
		}
	});
}

/**
 * 连接到Blender WebSocket服务器
 */
export function connectBlender(): void {
	if (isConnectedToBlender) {
		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text('已连接到Blender服务器'),
			'info'
		);
		return;
	}

	eda.sys_Message.showToastMessage(
		eda.sys_I18n.text('正在连接到Blender服务器...'),
		'info'
	);

	try {
		// 注册WebSocket连接
		eda.sys_WebSocket.register(
			BLENDER_WEBSOCKET_ID,
			DEFAULT_BLENDER_ADDRESS,
			handleBlenderMessage,
			onBlenderConnected
		);
	} catch (error) {
		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text('连接Blender失败: ') + (error as Error).message,
			'error'
		);
	}
}

/**
 * WebSocket连接建立时的回调
 */
function onBlenderConnected(): void {
	isConnectedToBlender = true;
	eda.sys_Message.showToastMessage(
		eda.sys_I18n.text('成功连接到Blender服务器!'),
		'success'
	);
}

/**
 * 处理来自Blender的WebSocket消息
 */
function handleBlenderMessage(event: MessageEvent<any>): void {
	try {
		const message = JSON.parse(event.data);
		
		switch (message.type) {
			case 'upload_progress':
				eda.sys_Message.showToastMessage(
					eda.sys_I18n.text(`上传进度: ${message.progress}% - ${message.status}`),
					'info'
				);
				break;
				
			case 'upload_complete':
				eda.sys_Message.showToastMessage(
					eda.sys_I18n.text('文件上传完成，正在导入到Blender...'),
					'success'
				);
				break;
				
			case 'import_complete':
				isRendering = false;
				eda.sys_Message.showToastMessage(
					eda.sys_I18n.text(`PCB导入完成: ${message.details || '成功导入PCB模型'}`),
					'success'
				);
				break;
				
			case 'error':
				isRendering = false;
				eda.sys_Message.showToastMessage(
					eda.sys_I18n.text(`Blender错误: ${message.message}`),
					'error'
				);
				break;
				
			case 'connection_confirmed':
				eda.sys_Message.showToastMessage(
					eda.sys_I18n.text('Blender连接已确认'),
					'success'
				);
				break;
				
			default:
				console.log('收到Blender消息:', message);
				if (message.message) {
					eda.sys_Message.showToastMessage(
						eda.sys_I18n.text(`Blender: ${message.message}`),
						'info'
					);
				}
		}
	} catch (error) {
		console.error('解析Blender消息失败:', error);
		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text('收到无效的Blender消息'),
			'warning'
		);
	}
}

/**
 * 渲染当前PCB到Blender - 统一的连接和渲染功能
 */
export async function renderPCBInBlender(): Promise<void> {
	if (isRendering) {
		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text('正在渲染中，请稍候...'),
			'info'
		);
		return;
	}

	try {
		isRendering = true;
		
		// 首先检查连接状态
		if (!isConnectedToBlender) {
			eda.sys_Message.showToastMessage(
				eda.sys_I18n.text('正在连接到Blender服务器...'),
				'info'
			);
			
			// 尝试连接到Blender
			await connectToBlenderAsync();
			
			// 等待连接建立
			if (!isConnectedToBlender) {
				throw new Error('无法连接到Blender服务器');
			}
		}
		
		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text('正在获取PCB 3D模型文件...'),
			'info'
		);

		// 获取当前PCB的3D模型文件
		const pcbFile = await eda.pcb_ManufactureData.get3DFile(
			'pcbModel',
			'obj',
			['Component Model', 'Silkscreen', 'Wire In Signal Layer']
			,'Parts'
		);

		if (!pcbFile) {
			throw new Error('无法获取PCB 3D模型文件');
		}

		// 显示文件信息
		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text(`PCB文件获取成功: ${pcbFile.name} (${(pcbFile.size / 1024).toFixed(2)} KB)`),
			'info'
		);

		// 使用FileReader读取File对象内容 - 参考HTML客户端的实现
		const fileArrayBuffer = await new Promise<ArrayBuffer>((resolve, reject) => {
			const reader = new FileReader();
			reader.onload = () => {
				if (reader.result instanceof ArrayBuffer) {
					resolve(reader.result);
				} else {
					reject(new Error('FileReader返回的不是ArrayBuffer'));
				}
			};
			reader.onerror = () => reject(reader.error || new Error('文件读取失败'));
			reader.readAsArrayBuffer(pcbFile);
		});

		// 验证文件大小
		if (fileArrayBuffer.byteLength === 0) {
			throw new Error('PCB文件数据为空');
		}

		// 准备发送的消息 - 完全参考HTML客户端的格式
		const filename = pcbFile.name.endsWith('.zip') ? pcbFile.name : `${pcbFile.name}.zip`;
		
		const message = {
			type: 'file_upload',
			filename: filename,
			size: pcbFile.size,
			data: Array.from(new Uint8Array(fileArrayBuffer))
		};

		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text(`正在发送文件到Blender: ${(fileArrayBuffer.byteLength / 1024).toFixed(2)} KB`),
			'info'
		);

		// 发送文件到Blender
		eda.sys_WebSocket.send(
			BLENDER_WEBSOCKET_ID,
			JSON.stringify(message)
		);

		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text('PCB文件已发送到Blender，等待处理...'),
			'success'
		);

	} catch (error) {
		isRendering = false;
		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text('渲染失败: ') + (error as Error).message,
			'error'
		);
	}
}

/**
 * 断开与Blender的连接
 */
export function disconnectBlender(): void {
	if (!isConnectedToBlender) {
		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text('未连接到Blender服务器'),
			'info'
		);
		return;
	}

	try {
		// 使用官方API关闭WebSocket连接
		eda.sys_WebSocket.close(
			BLENDER_WEBSOCKET_ID,
			1000, // 正常关闭状态码
			'用户主动断开连接'
		);
		
		isConnectedToBlender = false;
		isRendering = false;
		
		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text('已断开与Blender的连接'),
			'info'
		);
	} catch (error) {
		eda.sys_Message.showToastMessage(
			eda.sys_I18n.text('断开连接失败: ') + (error as Error).message,
			'error'
		);
	}
}

/**
 * 检查Blender连接状态
 */
export function checkBlenderConnection(): void {
	const status = isConnectedToBlender ? '已连接' : '未连接';
	const messageType = isConnectedToBlender ? 'success' : 'warning';
	
	eda.sys_Message.showToastMessage(
		eda.sys_I18n.text('Blender连接状态: ') + status,
		messageType
	);
}
