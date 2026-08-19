"""Runtime identity and fail-closed Deployment visibility for DataForge V7."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from ..config import Settings
from .models import DataForgeInstance, ProjectDeployment, utc_now
from .store import V7Store, new_id


@dataclass(frozen=True)
class InstanceContext:
    id: str
    code: str
    mode: str
    bound_deployment_id: str | None
    source_instance_id: str | None

    @classmethod
    def load(cls, store: V7Store, settings: Settings) -> "InstanceContext":
        mode = settings.instance_mode
        if mode not in {"central", "local"}:
            raise RuntimeError("DATAFORGE_INSTANCE_MODE 只允许 central 或 local")
        if not settings.instance_code:
            raise RuntimeError("DATAFORGE_INSTANCE_CODE 不能为空")
        with store.sessions.begin() as session:
            count = session.scalar(select(func.count()).select_from(DataForgeInstance)) or 0
            if count == 0:
                value = DataForgeInstance(
                    id=new_id("instance"), instance_code=settings.instance_code,
                    instance_mode=mode, created_at=utc_now(),
                )
                session.add(value)
            elif count == 1:
                value = session.scalar(select(DataForgeInstance))
                if not value:
                    raise RuntimeError("DataForge 实例身份不存在")
                if value.instance_mode != mode:
                    # A brand-new database can be migrated before the runtime mode
                    # is injected. Only an unbound, source-less bootstrap row may be
                    # adopted; an initialized local instance is immutable.
                    if value.bound_deployment_id or value.source_instance_id:
                        raise RuntimeError("运行模式与数据库实例身份不一致")
                    value.instance_mode = mode
                if value.instance_code != settings.instance_code:
                    if value.bound_deployment_id or value.source_instance_id:
                        raise RuntimeError("实例编码与数据库实例身份不一致")
                    value.instance_code = settings.instance_code
            else:
                raise RuntimeError("数据库中只能存在一个 DataForge 实例身份")
            session.flush()
            return cls(value.id, value.instance_code, value.instance_mode,
                       value.bound_deployment_id, value.source_instance_id)

    @property
    def initialized(self) -> bool:
        return self.mode == "central" or bool(self.bound_deployment_id)

    def require_central(self) -> None:
        if self.mode != "central":
            raise PermissionError("Local 实例不允许执行中心管理操作")

    def require_local(self) -> None:
        if self.mode != "local":
            raise PermissionError("Central 实例不允许执行本地导入操作")

    def require_deployment(self, store: V7Store, deployment_id: str) -> ProjectDeployment:
        with store.sessions() as session:
            value = session.get(ProjectDeployment, deployment_id)
            if not value or (self.mode == "local" and value.deployment_id != self.bound_deployment_id):
                raise LookupError("Deployment 不存在")
            return value

    def bind_seed(self, store: V7Store, deployment_id: str, source_instance_id: str) -> "InstanceContext":
        self.require_local()
        with store.sessions.begin() as session:
            value = session.get(DataForgeInstance, self.id)
            if not value:
                raise RuntimeError("DataForge 实例身份不存在")
            if value.bound_deployment_id:
                raise ValueError("Local 实例已完成 Seed，拒绝第二次 Seed")
            value.bound_deployment_id = deployment_id
            value.source_instance_id = source_instance_id
        return InstanceContext(self.id, self.code, self.mode, deployment_id, source_instance_id)

    def payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "instance_code": self.code,
            "instance_mode": self.mode,
            "bound_deployment_id": self.bound_deployment_id,
            "source_instance_id": self.source_instance_id,
            "initialized": self.initialized,
        }
