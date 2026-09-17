"""全局写锁（单一实例）。写操作（本体/项目保存、发布、恢复、凭据快写）共用；
网络探测与目录读取绝不持锁。路由模块与 server 共享同一把锁，不另行创建。"""
import threading

LOCK = threading.Lock()
