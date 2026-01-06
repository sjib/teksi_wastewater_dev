# QGIS 3.34.8+ Elevation Profile API 调查结果

## 概述

本文档记录了QGIS 3.34.8及以上版本中Elevation Profile API的调查结果。该API提供了用于创建和管理高程剖面视图的类和方法，可用于替代现有的基于QtWebKit的自定义实现。

## 主要API类

### 1. QgsElevationProfileCanvas

**类路径**: `qgis.gui.QgsElevationProfileCanvas`

**引入版本**: QGIS 3.26

**继承关系**: 继承自 `QgsPlotCanvas`

**描述**: 用于显示高程剖面的画布widget，提供了绘制和交互高程剖面的功能。这是可嵌入的widget，可以直接在插件中使用。

**主要方法**:

- `__init__(parent: QWidget = None)`: 构造函数，创建一个新的高程剖面画布实例
- `setProfileCurve(curve: QgsGeometry)`: 设置剖面曲线（路径）
- `setTolerance(tolerance: float)`: 设置剖面容差值，确定从`profileCurve()`到包含结果的最大距离
- `setSources(sources: List[QgsAbstractProfileSource])`: 设置要包含在剖面中的数据源列表
- `axisScaleRatio() -> float`: 返回绘图中水平（距离）与垂直（高程）比例的当前比率
- `cancelJobs()`: 取消任何正在进行的渲染作业

**主要信号**:

- `activeJobCountChanged(int count)`: 当活动的后台作业数量发生变化时触发

**使用示例**:
```python
from qgis.gui import QgsElevationProfileCanvas
from qgis.core import QgsGeometry

# 创建canvas
canvas = QgsElevationProfileCanvas(parent_widget)

# 设置剖面曲线（从现有路径创建）
profile_curve = QgsGeometry.fromPolylineXY([...])  # 路径点列表
canvas.setProfileCurve(profile_curve)

# 设置容差
canvas.setTolerance(10.0)  # 10个地图单位

# 设置数据源
sources = [...]  # QgsAbstractProfileSource列表
canvas.setSources(sources)
```

### 2. QgsElevationProfile

**类路径**: `qgis.core.QgsElevationProfile`

**描述**: 表示附加到项目的高程剖面。该类提供了管理剖面属性的方法。

**主要方法**:

- `setCrs(crs: QgsCoordinateReferenceSystem)`: 设置坐标参考系统
- `setDistanceUnit(unit: QgsUnitTypes.DistanceUnit)`: 设置距离单位
- `setProfileCurve(curve: QgsGeometry)`: 设置剖面曲线
- `crs() -> QgsCoordinateReferenceSystem`: 返回与剖面地图坐标关联的坐标参考系统
- `distanceUnit() -> QgsUnitTypes.DistanceUnit`: 返回剖面使用的距离单位
- `profileCurve() -> QgsGeometry`: 返回剖面曲线

### 3. QgsAbstractProfileSource

**类路径**: `qgis.core.QgsAbstractProfileSource`

**描述**: 抽象基类，用于定义高程剖面的数据源。所有具体的数据源类都继承自此类。

**主要子类**:

- `QgsProfileSourceVectorLayer`: 用于矢量图层的数据源
- `QgsProfileSourceRasterLayer`: 用于栅格图层的数据源

**主要方法**:

- `generateProfile(curve: QgsGeometry, context: QgsProfileGenerationContext) -> QgsProfileResults`: 生成剖面数据

### 4. QgsProfileSourceVectorLayer

**类路径**: `qgis.core.QgsProfileSourceVectorLayer`

**描述**: 用于从矢量图层生成高程剖面的数据源。

**主要方法**:

- `__init__(layer: QgsVectorLayer)`: 构造函数，接受一个矢量图层
- `setElevationAttribute(attribute: str)`: 设置用于高程的属性字段名
- `setElevationAttributeIndex(index: int)`: 设置用于高程的属性字段索引

**使用示例**:
```python
from qgis.core import QgsProfileSourceVectorLayer, QgsVectorLayer

# 获取矢量图层
layer = QgsProject.instance().mapLayersByName("vw_tww_reach")[0]

# 创建数据源
source = QgsProfileSourceVectorLayer(layer)
source.setElevationAttribute("bottom_level")  # 设置高程字段

# 添加到canvas
canvas.setSources([source])
```

### 5. QgsProfileSourceRasterLayer

**类路径**: `qgis.core.QgsProfileSourceRasterLayer`

**描述**: 用于从栅格图层（如DEM）生成高程剖面的数据源。

**主要方法**:

- `__init__(layer: QgsRasterLayer)`: 构造函数，接受一个栅格图层

**使用示例**:
```python
from qgis.core import QgsProfileSourceRasterLayer, QgsRasterLayer

# 获取栅格图层（DEM）
dem_layer = QgsProject.instance().mapLayersByName("DEM")[0]

# 创建数据源
source = QgsProfileSourceRasterLayer(dem_layer)

# 添加到canvas
canvas.setSources([source])
```

### 6. QgsLayoutElevationProfileWidget

**类路径**: `qgis.gui.QgsLayoutElevationProfileWidget`

**引入版本**: QGIS 3.30

**描述**: 用于布局高程剖面项设置的控件。**注意**: 该类不属于公共API，主要用于QGIS内部的布局系统。

**主要方法**:

- `copySettingsFromProfileCanvas(canvas: QgsElevationProfileCanvas)`: 从高程剖面画布复制选定的设置
- `createExpressionContext() -> QgsExpressionContext`: 创建表达式上下文
- `setDesignerInterface(iface: QgsLayoutDesignerInterface)`: 设置设计器接口
- `setMasterLayout(masterLayout: QgsMasterLayoutInterface)`: 设置主布局

**注意**: 该类主要用于QGIS的打印布局系统，对于插件开发可能不太适用。

### 7. QgsElevationMap

**类路径**: `qgis.core.QgsElevationMap`

**引入版本**: QGIS 3.28

**描述**: 用于存储数字高程模型（DEM）的栅格图像，可在地图图层渲染过程中更新。

**主要方法**:

- `applyEyeDomeLighting(image: QImage)`: 对给定图像应用眼罩照明效果
- `applyHillshading(image: QImage)`: 对给定图像应用山体阴影效果
- `combine(otherElevationMap: QgsElevationMap, method: Qgis.ElevationMapCombineMethod)`: 合并其他高程地图
- `isNoData(colorRaw: int) -> bool`: 判断编码值是否为无数据值

**注意**: 该类主要用于3D渲染，对于2D剖面视图可能不太直接相关。

## 实现方案分析

### 方案1: 使用QgsElevationProfileCanvas（推荐）

**优点**:
- 直接使用QGIS内置的Elevation Profile功能
- 继承自`QgsPlotCanvas`，提供完整的交互功能（缩放、平移等）
- 支持多种数据源（矢量图层、栅格图层）
- 自动处理高程数据的提取和渲染
- 与QGIS的Elevation Profile面板功能一致

**实现步骤**:
1. 创建`QgsElevationProfileCanvas`实例
2. 从现有的`TwwProfile`数据中提取路径，转换为`QgsGeometry`
3. 使用`setProfileCurve()`设置剖面路径
4. 创建`QgsProfileSourceVectorLayer`数据源，指向相关的矢量图层
5. 使用`setSources()`添加数据源
6. 将canvas添加到`TwwProfileDockWidget`中

**注意事项**:
- 需要将现有的`TwwProfile`数据结构转换为QGIS的格式
- 需要确保矢量图层包含高程字段（如`bottom_level`、`co_level`等）
- 可能需要配置图层的Elevation属性（通过QGIS的图层属性对话框）

### 方案2: 使用QgsPlotCanvas自定义绘制（备选）

**优点**:
- 完全控制绘制过程
- 可以保留现有的数据结构和逻辑
- 可以自定义样式和交互

**缺点**:
- 需要手动实现所有绘制逻辑
- 工作量大，维护成本高
- 可能无法利用QGIS的优化功能

**实现步骤**:
1. 创建`QgsPlotCanvas`实例
2. 重写绘制方法，使用现有的`TwwProfile`数据
3. 实现缩放、平移等交互功能
4. 集成到`TwwProfileDockWidget`中

## 数据转换需求

### 从TwwProfile到QgsGeometry

现有的`TwwProfile`类包含路径信息，需要转换为`QgsGeometry`对象：

```python
from qgis.core import QgsGeometry, QgsPointXY

def tww_profile_to_qgs_geometry(profile: TwwProfile) -> QgsGeometry:
    """
    将TwwProfile转换为QgsGeometry路径
    """
    points = []
    # 从profile中提取路径点
    # 根据TwwProfile的实际结构提取点坐标
    for element in profile.elements:
        if hasattr(element, 'points'):
            for point in element.points:
                points.append(QgsPointXY(point.x, point.y))
    
    return QgsGeometry.fromPolylineXY(points)
```

### 数据源配置

需要确保矢量图层包含高程信息，并正确配置：

1. **高程字段**: 确保图层有高程字段（如`bottom_level`、`co_level`等）
2. **图层配置**: 在QGIS中，可以通过图层属性对话框的"Elevation"选项卡配置高程属性
3. **数据源创建**: 使用`QgsProfileSourceVectorLayer`时，需要指定正确的高程字段

## API可用性检查

在实现之前，应该检查API的可用性：

```python
def check_elevation_profile_api():
    """
    检查QGIS Elevation Profile API是否可用
    """
    try:
        from qgis.gui import QgsElevationProfileCanvas
        from qgis.core import QgsProfileSourceVectorLayer
        return True
    except ImportError:
        return False
```

## 版本兼容性

- **QgsElevationProfileCanvas**: 自QGIS 3.26起可用
- **QgsProfileSourceVectorLayer**: 自QGIS 3.26起可用
- **QgsProfileSourceRasterLayer**: 自QGIS 3.26起可用
- **QgsLayoutElevationProfileWidget**: 自QGIS 3.30起可用（但非公共API）

**插件要求**: QGIS 3.34.8+，所有核心API类都可用。

## 参考资源

1. **QGIS API文档**:
   - https://api.qgis.org/api/classQgsElevationProfileCanvas.html
   - https://api.qgis.org/api/classQgsLayoutElevationProfileWidget.html

2. **QGIS Python API文档**:
   - https://qgis.org/pyqgis/master/core/QgsElevationProfile.html
   - https://qgis.org/pyqgis/master/core/QgsElevationMap.html

3. **QGIS用户手册**:
   - QGIS 3.34用户指南第11.3节"高程剖面视图"
   - https://docs.qgis.org/3.34/pdf/en/QGIS-3.34-DesktopUserGuide-en.pdf

## 下一步行动

1. **创建新的Widget类**: 创建`TwwElevationProfileWidget`类，包装`QgsElevationProfileCanvas`
2. **实现数据转换**: 创建从`TwwProfile`到QGIS格式的转换函数
3. **集成到Dock**: 将新widget集成到`TwwProfileDockWidget`中
4. **测试**: 测试新实现的功能和性能

## 结论

QGIS 3.34.8+提供了完整的Elevation Profile API，主要包括：

1. **QgsElevationProfileCanvas**: 可嵌入的widget，用于显示高程剖面
2. **QgsProfileSourceVectorLayer/QgsProfileSourceRasterLayer**: 数据源类，用于从图层生成剖面数据
3. **QgsElevationProfile**: 管理剖面属性的类

**推荐方案**: 使用`QgsElevationProfileCanvas`作为主要实现方案，因为它提供了完整的交互功能和自动的数据处理能力。

