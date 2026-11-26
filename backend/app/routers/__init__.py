"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import cross_module, gap_contract, resource

ROUTERS = [cross_module, gap_contract, resource]  # 固定前缀路由须在动态资源路由之前
