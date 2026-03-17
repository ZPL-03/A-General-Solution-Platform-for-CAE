/**
 * @file fem_solver.h
 * @brief CAE 有限元求解核心的头文件
 *
 * 当前版本聚焦于 3D 四节点四面体单元（Tet4）的线弹性静力学分析。
 * 设计目标：
 * 1. 让 Python 前端可以稳定调用；
 * 2. 为后续加入更多单元类型、非线性分析和 Fortran 扩展留出接口；
 * 3. 尽量用清晰的中文注释帮助初学者理解代码结构。
 */

#ifndef CAE_FEM_SOLVER_H
#define CAE_FEM_SOLVER_H

#include <memory>
#include <string>
#include <vector>

#include <Eigen/Dense>
#include <Eigen/Sparse>

/**
 * @brief 节点类
 *
 * 一个节点包含：
 * 1. 节点编号 id；
 * 2. 节点空间坐标 coords；
 * 3. 求解后的位移 displacement。
 */
class Node {
public:
    int id;
    Eigen::Vector3d coords;
    Eigen::Vector3d displacement;

    Node(int node_id, double x, double y, double z);
};

/**
 * @brief 材料类
 *
 * 当前只实现各向同性线弹性材料。
 */
class Material {
public:
    int id;
    double E;
    double nu;
    double rho;
    double k;
    double yield_strength;
    double hardening_modulus;
    bool nonlinear_enabled;

    Material(
        int material_id,
        double young_modulus,
        double poisson_ratio,
        double density = 0.0,
        double thermal_conductivity = 45.0,
        double input_yield_strength = 0.0,
        double input_hardening_modulus = 0.0,
        bool input_nonlinear_enabled = false
    );

    /**
     * @brief 计算 3D 各向同性线弹性本构矩阵 D（6x6）
     */
    Eigen::Matrix<double, 6, 6> getElasticityMatrix() const;
    Eigen::Matrix<double, 6, 6> getElasticityMatrixForModulus(double effective_E) const;
    Eigen::Matrix<double, 6, 6> getTangentMatrix(const Eigen::VectorXd& strain) const;
    Eigen::VectorXd getStressFromStrain(const Eigen::VectorXd& strain) const;
};

/**
 * @brief 单元基类
 *
 * 后续如果加入 Hex8、Beam、Shell 等单元，可以继续从这个基类派生。
 */
class Element {
public:
    int id;
    std::vector<int> node_ids;
    int material_id;

    virtual ~Element() = default;

    virtual Eigen::MatrixXd getStiffnessMatrix(
        const std::vector<Node>& nodes,
        const Material& material
    ) const = 0;

    virtual Eigen::VectorXd getStrain(
        const std::vector<Node>& nodes,
        const Eigen::VectorXd& element_displacements
    ) const = 0;

    virtual Eigen::VectorXd getStress(
        const std::vector<Node>& nodes,
        const Material& material,
        const Eigen::VectorXd& element_displacements
    ) const = 0;

    virtual Eigen::MatrixXd getTangentStiffnessMatrix(
        const std::vector<Node>& nodes,
        const Material& material,
        const Eigen::VectorXd& element_displacements
    ) const = 0;

    virtual Eigen::VectorXd getInternalForce(
        const std::vector<Node>& nodes,
        const Material& material,
        const Eigen::VectorXd& element_displacements
    ) const = 0;

    virtual double computeVolume(const std::vector<Node>& nodes) const = 0;
    virtual Eigen::MatrixXd getThermalConductivityMatrix(
        const std::vector<Node>& nodes,
        const Material& material
    ) const = 0;
    virtual Eigen::Vector3d getTemperatureGradient(
        const std::vector<Node>& nodes,
        const Eigen::VectorXd& nodal_temperatures
    ) const = 0;
};

/**
 * @brief 4 节点四面体单元
 */
class Tet4Element : public Element {
public:
    Tet4Element(int element_id, const std::vector<int>& element_node_ids, int element_material_id);

    Eigen::MatrixXd getStiffnessMatrix(
        const std::vector<Node>& nodes,
        const Material& material
    ) const override;

    Eigen::VectorXd getStrain(
        const std::vector<Node>& nodes,
        const Eigen::VectorXd& element_displacements
    ) const override;

    Eigen::VectorXd getStress(
        const std::vector<Node>& nodes,
        const Material& material,
        const Eigen::VectorXd& element_displacements
    ) const override;

    Eigen::MatrixXd getTangentStiffnessMatrix(
        const std::vector<Node>& nodes,
        const Material& material,
        const Eigen::VectorXd& element_displacements
    ) const override;

    Eigen::VectorXd getInternalForce(
        const std::vector<Node>& nodes,
        const Material& material,
        const Eigen::VectorXd& element_displacements
    ) const override;

    double computeVolume(const std::vector<Node>& nodes) const override;
    Eigen::MatrixXd getThermalConductivityMatrix(
        const std::vector<Node>& nodes,
        const Material& material
    ) const override;
    Eigen::Vector3d getTemperatureGradient(
        const std::vector<Node>& nodes,
        const Eigen::VectorXd& nodal_temperatures
    ) const override;

private:
    /**
     * @brief 构造单元雅可比矩阵 J
     *
     * J 负责把局部自然坐标系中的导数转换到全局坐标系。
     */
    Eigen::Matrix3d buildJacobian(const std::vector<Node>& nodes) const;

    /**
     * @brief 构造应变-位移矩阵 B（6x12）
     */
    Eigen::Matrix<double, 6, 12> buildBMatrix(const std::vector<Node>& nodes) const;

    /**
     * @brief 检查四面体单元的节点编号是否合法
     */
    void validateNodeAccess(const std::vector<Node>& nodes) const;
};

/**
 * @brief 8 节点六面体单元
 *
 * 当前实现基于等参 Hex8 单元与 2x2x2 高斯积分。
 */
class Hex8Element : public Element {
public:
    Hex8Element(int element_id, const std::vector<int>& element_node_ids, int element_material_id);

    Eigen::MatrixXd getStiffnessMatrix(
        const std::vector<Node>& nodes,
        const Material& material
    ) const override;

    Eigen::VectorXd getStrain(
        const std::vector<Node>& nodes,
        const Eigen::VectorXd& element_displacements
    ) const override;

    Eigen::VectorXd getStress(
        const std::vector<Node>& nodes,
        const Material& material,
        const Eigen::VectorXd& element_displacements
    ) const override;

    Eigen::MatrixXd getTangentStiffnessMatrix(
        const std::vector<Node>& nodes,
        const Material& material,
        const Eigen::VectorXd& element_displacements
    ) const override;

    Eigen::VectorXd getInternalForce(
        const std::vector<Node>& nodes,
        const Material& material,
        const Eigen::VectorXd& element_displacements
    ) const override;

    double computeVolume(const std::vector<Node>& nodes) const override;
    Eigen::MatrixXd getThermalConductivityMatrix(
        const std::vector<Node>& nodes,
        const Material& material
    ) const override;
    Eigen::Vector3d getTemperatureGradient(
        const std::vector<Node>& nodes,
        const Eigen::VectorXd& nodal_temperatures
    ) const override;

private:
    void validateNodeAccess(const std::vector<Node>& nodes) const;
    Eigen::Matrix<double, 8, 3> buildNaturalGradients(double xi, double eta, double zeta) const;
    Eigen::Matrix3d buildJacobian(
        const std::vector<Node>& nodes,
        const Eigen::Matrix<double, 8, 3>& natural_gradients
    ) const;
    Eigen::Matrix<double, 6, 24> buildBMatrix(
        const std::vector<Node>& nodes,
        double xi,
        double eta,
        double zeta,
        double& det_j
    ) const;
};

/**
 * @brief 位移边界条件
 *
 * dof 取值：
 * 0 -> X 向位移
 * 1 -> Y 向位移
 * 2 -> Z 向位移
 */
class BoundaryCondition {
public:
    int node_id;
    int dof;
    double value;

    BoundaryCondition(int constrained_node_id, int constrained_dof, double constrained_value);
};

/**
 * @brief 节点载荷
 */
class Load {
public:
    int node_id;
    Eigen::Vector3d force;

    Load(int loaded_node_id, double fx, double fy, double fz);
};

/**
 * @brief 热边界条件
 *
 * 一个节点对应一个标量温度自由度。
 */
class ThermalBoundaryCondition {
public:
    int node_id;
    double temperature;

    ThermalBoundaryCondition(int constrained_node_id, double constrained_temperature);
};

/**
 * @brief 热载荷
 *
 * 这里使用节点等效热流输入，单位可以按前端约定理解为 W。
 */
class ThermalLoad {
public:
    int node_id;
    double value;

    ThermalLoad(int loaded_node_id, double thermal_value);
};

/**
 * @brief 有限元求解器主类
 *
 * 职责：
 * 1. 存储节点、单元、材料、边界条件、载荷；
 * 2. 组装全局刚度矩阵和载荷向量；
 * 3. 调用稀疏线性求解器；
 * 4. 提供位移、应力、应变等结果给 Python 前端。
 */
class FEMSolver {
public:
    FEMSolver();

    // ---------- 模型输入接口 ----------
    void addNode(int id, double x, double y, double z);
    void addElement(const std::shared_ptr<Element>& elem);
    void addMaterial(const Material& mat);
    void addBoundaryCondition(int node_id, int dof, double value);
    void addLoad(int node_id, double fx, double fy, double fz);
    void addThermalBoundaryCondition(int node_id, double temperature);
    void addThermalLoad(int node_id, double value);

    // ---------- 求解流程 ----------
    void initialize();
    void assembleGlobalStiffnessMatrix();
    Eigen::VectorXd assembleGlobalInternalForceVector() const;
    void assembleGlobalForceVector();
    void applyBoundaryConditions();
    void solve();
    void updateNodalDisplacements();

    /**
     * @brief 一键执行完整线弹性静力学流程
     */
    void runLinearStaticAnalysis();

    /**
     * @brief 增量-Newton 静力学流程
     *
     * 说明：
     * 1. 当前单元仍为线弹性单元，因此该流程更多是为后续真实非线性求解预留框架；
     * 2. 目前已经具备载荷步和残差迭代控制能力；
     * 3. 后续加入几何/材料非线性后，可在这个流程内替换切线刚度与残差计算。
     */
    void runNonlinearStaticAnalysis(int load_steps, int max_iterations, double tolerance);

    /**
     * @brief 线性模态分析
     *
     * 基于刚度矩阵 K 与集总质量矩阵 M，求解广义特征值问题
     * K * phi = lambda * M * phi。
     */
    void runModalAnalysis(int mode_count);

    /**
     * @brief 线性瞬态动力学分析
     *
     * 使用 Newmark-beta 时间积分格式求解：
     * M a + C v + K u = F(t)
     */
    void runTransientAnalysis(
        double total_time,
        double time_step,
        double beta,
        double gamma,
        double damping_alpha,
        double damping_beta
    );

    /**
     * @brief 线性频响分析
     *
     * 在频率区间内逐点求解稳态谐响应幅值。
     */
    void runFrequencyResponseAnalysis(
        double start_frequency_hz,
        double end_frequency_hz,
        int point_count,
        double damping_alpha,
        double damping_beta
    );
    void runSteadyStateThermalAnalysis();

    // ---------- 线性代数求解器选择 ----------
    void setLinearSolverType(const std::string& solver_type);
    std::string getLinearSolverType() const;

    // ---------- 并行控制 ----------
    void setParallelEnabled(bool enabled);
    bool isParallelEnabled() const;
    void setNumThreads(int threads);
    int getNumThreads() const;
    int getAvailableThreads() const;

    // ---------- 求解诊断 ----------
    bool hasConverged() const;
    int getLastIterationCount() const;
    double getLastResidualNorm() const;

    // ---------- 后处理接口 ----------
    std::vector<double> getDisplacements() const;
    std::vector<double> getStresses() const;
    std::vector<double> getStrains() const;
    std::vector<double> getMeshBounds() const;
    std::vector<double> getModalFrequenciesHz() const;
    std::vector<double> getModeShape(int mode_index) const;
    std::vector<double> getTransientTimes() const;
    std::vector<double> getTransientMaxDisplacements() const;
    std::vector<double> getTransientLoadedUxHistory() const;
    std::vector<double> getTransientLoadedUyHistory() const;
    std::vector<double> getTransientLoadedUzHistory() const;
    std::vector<double> getTransientLoadedUmagHistory() const;
    std::vector<double> getFrequencyResponseFrequenciesHz() const;
    std::vector<double> getFrequencyResponseMaxDisplacements() const;
    std::vector<double> getFrequencyResponseLoadedUxHistory() const;
    std::vector<double> getFrequencyResponseLoadedUyHistory() const;
    std::vector<double> getFrequencyResponseLoadedUzHistory() const;
    std::vector<double> getFrequencyResponseLoadedUmagHistory() const;
    std::vector<double> getTemperatures() const;
    std::vector<double> getHeatFluxMagnitudes() const;

    // ---------- 输出与调试 ----------
    void exportResults(const std::string& filename) const;
    void printInfo() const;

    // ---------- 统计信息 ----------
    std::size_t getNodeCount() const;
    std::size_t getElementCount() const;
    std::size_t getMaterialCount() const;
    std::size_t getBoundaryConditionCount() const;
    std::size_t getLoadCount() const;
    int getNumDofs() const;

private:
    std::vector<Node> nodes;
    std::vector<std::shared_ptr<Element>> elements;
    std::vector<Material> materials;
    std::vector<BoundaryCondition> bcs;
    std::vector<Load> loads;
    std::vector<ThermalBoundaryCondition> thermal_bcs;
    std::vector<ThermalLoad> thermal_loads;

    int num_dofs;
    int num_thermal_dofs;
    bool parallel_enabled;
    int num_threads;
    std::string linear_solver_type;
    bool last_converged;
    int last_iteration_count;
    double last_residual_norm;
    Eigen::SparseMatrix<double> K_global;
    Eigen::SparseMatrix<double> M_global;
    Eigen::SparseMatrix<double> K_thermal_global;
    Eigen::VectorXd F_global;
    Eigen::VectorXd U_global;
    Eigen::VectorXd Q_thermal_global;
    Eigen::VectorXd T_global;
    std::vector<double> modal_frequencies_hz;
    std::vector<Eigen::VectorXd> modal_shapes;
    std::vector<double> transient_times;
    std::vector<double> transient_max_displacements;
    std::vector<double> transient_loaded_ux_history;
    std::vector<double> transient_loaded_uy_history;
    std::vector<double> transient_loaded_uz_history;
    std::vector<double> transient_loaded_umag_history;
    std::vector<double> frequency_response_frequencies_hz;
    std::vector<double> frequency_response_max_displacements;
    std::vector<double> frequency_response_loaded_ux_history;
    std::vector<double> frequency_response_loaded_uy_history;
    std::vector<double> frequency_response_loaded_uz_history;
    std::vector<double> frequency_response_loaded_umag_history;
    std::vector<double> thermal_heat_flux_magnitudes;

    const Material& getMaterialById(int material_id) const;
    Eigen::VectorXd buildElementDisplacementVector(const Element& element) const;
    Eigen::VectorXd buildElementTemperatureVector(const Element& element) const;
    void assembleGlobalMassMatrix();
    void assembleGlobalThermalConductivityMatrix();
    void assembleGlobalThermalLoadVector();
    Eigen::VectorXd solveLinearSystem(
        const Eigen::SparseMatrix<double>& matrix,
        const Eigen::VectorXd& rhs
    ) const;
    void applyThermalBoundaryConditions();
    void validateNodeId(int node_id, const std::string& scene) const;
    void ensureModelReadyForSolution() const;
};

#endif  // CAE_FEM_SOLVER_H
