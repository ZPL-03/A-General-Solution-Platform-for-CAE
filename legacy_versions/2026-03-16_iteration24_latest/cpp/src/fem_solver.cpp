/**
 * @file fem_solver.cpp
 * @brief CAE 有限元求解核心实现文件
 */

#include "fem_solver.h"

#include <algorithm>
#include <cmath>
#include <complex>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>

#include <Eigen/IterativeLinearSolvers>
#include <Eigen/Eigenvalues>
#include <Eigen/SparseLU>

#ifdef _OPENMP
#include <omp.h>
#endif

namespace {

/**
 * @brief 计算 3D Von Mises 等效应力
 */
double computeVonMisesStress(const Eigen::VectorXd& stress) {
    if (stress.size() != 6) {
        throw std::runtime_error("应力向量长度必须为 6。");
    }

    const double sx = stress(0);
    const double sy = stress(1);
    const double sz = stress(2);
    const double txy = stress(3);
    const double tyz = stress(4);
    const double tzx = stress(5);

    return std::sqrt(
        0.5 * (
            std::pow(sx - sy, 2.0) +
            std::pow(sy - sz, 2.0) +
            std::pow(sz - sx, 2.0) +
            6.0 * (txy * txy + tyz * tyz + tzx * tzx)
        )
    );
}

/**
 * @brief 根据 3D 工程应变向量估算等效应变
 */
double computeEquivalentStrain(const Eigen::VectorXd& strain) {
    if (strain.size() != 6) {
        throw std::runtime_error("应变向量长度必须为 6。");
    }

    const double ex = strain(0);
    const double ey = strain(1);
    const double ez = strain(2);
    const double gxy = strain(3);
    const double gyz = strain(4);
    const double gzx = strain(5);

    return std::sqrt(
        (2.0 / 3.0) * (
            ex * ex + ey * ey + ez * ez +
            0.5 * (gxy * gxy + gyz * gyz + gzx * gzx)
        )
    );
}

/**
 * @brief 统计一组加载节点的平均位移响应分量。
 */
void computeLoadedResponseAverages(
    const std::vector<Load>& loads,
    const std::vector<Node>& nodes,
    const Eigen::VectorXd& displacement,
    double& avg_ux,
    double& avg_uy,
    double& avg_uz,
    double& avg_umag
) {
    avg_ux = 0.0;
    avg_uy = 0.0;
    avg_uz = 0.0;
    avg_umag = 0.0;

    if (loads.empty()) {
        return;
    }

    std::vector<int> loaded_node_ids;
    loaded_node_ids.reserve(loads.size());
    for (const auto& load : loads) {
        loaded_node_ids.push_back(load.node_id);
    }
    std::sort(loaded_node_ids.begin(), loaded_node_ids.end());
    loaded_node_ids.erase(std::unique(loaded_node_ids.begin(), loaded_node_ids.end()), loaded_node_ids.end());

    if (loaded_node_ids.empty()) {
        return;
    }

    for (const int node_id : loaded_node_ids) {
        if (node_id < 0 || node_id >= static_cast<int>(nodes.size())) {
            continue;
        }
        const Eigen::Vector3d node_displacement = displacement.segment<3>(node_id * 3);
        avg_ux += node_displacement(0);
        avg_uy += node_displacement(1);
        avg_uz += node_displacement(2);
        avg_umag += node_displacement.norm();
    }

    const double count = static_cast<double>(loaded_node_ids.size());
    avg_ux /= count;
    avg_uy /= count;
    avg_uz /= count;
    avg_umag /= count;
}

}  // namespace

namespace {

/**
 * @brief 统一获取当前编译环境下可用的最大线程数
 */
int detectAvailableThreads() {
#ifdef _OPENMP
    return omp_get_max_threads();
#else
    return 1;
#endif
}

}  // namespace

// ------------------------------------------------------------
// Node
// ------------------------------------------------------------

Node::Node(int node_id, double x, double y, double z)
    : id(node_id), coords(x, y, z), displacement(0.0, 0.0, 0.0) {}

// ------------------------------------------------------------
// Material
// ------------------------------------------------------------

Material::Material(
    int material_id,
    double young_modulus,
    double poisson_ratio,
    double density,
    double thermal_conductivity,
    double input_yield_strength,
    double input_hardening_modulus,
    bool input_nonlinear_enabled
) : id(material_id),
    E(young_modulus),
    nu(poisson_ratio),
    rho(density),
    k(thermal_conductivity),
    yield_strength(input_yield_strength),
    hardening_modulus(input_hardening_modulus),
    nonlinear_enabled(input_nonlinear_enabled) {}

Eigen::Matrix<double, 6, 6> Material::getElasticityMatrix() const {
    return getElasticityMatrixForModulus(E);
}

Eigen::Matrix<double, 6, 6> Material::getElasticityMatrixForModulus(double effective_E) const {
    if (effective_E <= 0.0) {
        throw std::runtime_error("材料等效杨氏模量必须大于 0。");
    }
    if (E <= 0.0) {
        throw std::runtime_error("材料杨氏模量 E 必须大于 0。");
    }
    if (nu <= -1.0 || nu >= 0.5) {
        throw std::runtime_error("材料泊松比 nu 必须位于 (-1, 0.5) 区间内。");
    }

    Eigen::Matrix<double, 6, 6> D = Eigen::Matrix<double, 6, 6>::Zero();

    const double lambda = effective_E * nu / ((1.0 + nu) * (1.0 - 2.0 * nu));
    const double mu = effective_E / (2.0 * (1.0 + nu));

    D(0, 0) = lambda + 2.0 * mu;
    D(1, 1) = lambda + 2.0 * mu;
    D(2, 2) = lambda + 2.0 * mu;

    D(0, 1) = lambda;
    D(0, 2) = lambda;
    D(1, 0) = lambda;
    D(1, 2) = lambda;
    D(2, 0) = lambda;
    D(2, 1) = lambda;

    D(3, 3) = mu;
    D(4, 4) = mu;
    D(5, 5) = mu;

    return D;
}

Eigen::Matrix<double, 6, 6> Material::getTangentMatrix(const Eigen::VectorXd& strain) const {
    if (!nonlinear_enabled || yield_strength <= 0.0) {
        return getElasticityMatrix();
    }

    const double equivalent_strain = computeEquivalentStrain(strain);
    const double yield_strain = yield_strength / std::max(E, 1.0e-12);
    if (equivalent_strain <= yield_strain) {
        return getElasticityMatrix();
    }

    const double plastic_tangent = hardening_modulus > 0.0
        ? (E * hardening_modulus) / (E + hardening_modulus)
        : std::max(E * 1.0e-6, 1.0);
    return getElasticityMatrixForModulus(plastic_tangent);
}

Eigen::VectorXd Material::getStressFromStrain(const Eigen::VectorXd& strain) const {
    const Eigen::Matrix<double, 6, 6> D_elastic = getElasticityMatrix();
    const Eigen::VectorXd elastic_stress = D_elastic * strain;

    if (!nonlinear_enabled || yield_strength <= 0.0) {
        return elastic_stress;
    }

    const double equivalent_strain = computeEquivalentStrain(strain);
    if (equivalent_strain <= std::numeric_limits<double>::epsilon()) {
        return elastic_stress;
    }

    const double equivalent_stress_elastic = computeVonMisesStress(elastic_stress);
    const double yield_strain = yield_strength / std::max(E, 1.0e-12);
    if (equivalent_strain <= yield_strain || equivalent_stress_elastic <= std::numeric_limits<double>::epsilon()) {
        return elastic_stress;
    }

    const double plastic_tangent = hardening_modulus > 0.0
        ? (E * hardening_modulus) / (E + hardening_modulus)
        : std::max(E * 1.0e-6, 1.0);
    const double equivalent_stress = yield_strength + plastic_tangent * (equivalent_strain - yield_strain);
    const double stress_scale = equivalent_stress / equivalent_stress_elastic;
    return stress_scale * elastic_stress;
}

// ------------------------------------------------------------
// Tet4Element
// ------------------------------------------------------------

Tet4Element::Tet4Element(int element_id, const std::vector<int>& element_node_ids, int element_material_id) {
    if (element_node_ids.size() != 4) {
        throw std::runtime_error("Tet4 单元必须恰好包含 4 个节点编号。");
    }

    id = element_id;
    node_ids = element_node_ids;
    material_id = element_material_id;
}

void Tet4Element::validateNodeAccess(const std::vector<Node>& nodes) const {
    for (const int node_id : node_ids) {
        if (node_id < 0 || node_id >= static_cast<int>(nodes.size())) {
            std::ostringstream oss;
            oss << "Tet4 单元 " << id << " 引用了不存在的节点编号 " << node_id << "。";
            throw std::runtime_error(oss.str());
        }
    }
}

Eigen::Matrix3d Tet4Element::buildJacobian(const std::vector<Node>& nodes) const {
    validateNodeAccess(nodes);

    const Eigen::Vector3d& x1 = nodes[node_ids[0]].coords;
    const Eigen::Vector3d& x2 = nodes[node_ids[1]].coords;
    const Eigen::Vector3d& x3 = nodes[node_ids[2]].coords;
    const Eigen::Vector3d& x4 = nodes[node_ids[3]].coords;

    Eigen::Matrix3d J;
    J.col(0) = x2 - x1;
    J.col(1) = x3 - x1;
    J.col(2) = x4 - x1;

    return J;
}

double Tet4Element::computeVolume(const std::vector<Node>& nodes) const {
    const Eigen::Matrix3d J = buildJacobian(nodes);
    const double volume = std::abs(J.determinant()) / 6.0;

    if (volume <= std::numeric_limits<double>::epsilon()) {
        std::ostringstream oss;
        oss << "Tet4 单元 " << id << " 的体积接近 0，可能存在退化单元。";
        throw std::runtime_error(oss.str());
    }

    return volume;
}

Eigen::MatrixXd Tet4Element::getThermalConductivityMatrix(
    const std::vector<Node>& nodes,
    const Material& material
) const {
    const Eigen::Matrix3d J = buildJacobian(nodes);
    const double det_j = J.determinant();
    if (std::abs(det_j) <= std::numeric_limits<double>::epsilon()) {
        throw std::runtime_error("Tet4 热传导矩阵组装失败：雅可比行列式接近 0。");
    }
    if (material.k <= 0.0) {
        throw std::runtime_error("材料导热系数必须大于 0。");
    }

    Eigen::Matrix<double, 4, 3> natural_gradients;
    natural_gradients <<
        -1.0, -1.0, -1.0,
         1.0,  0.0,  0.0,
         0.0,  1.0,  0.0,
         0.0,  0.0,  1.0;
    const Eigen::Matrix3d J_inv = J.inverse();
    const Eigen::Matrix<double, 4, 3> global_gradients = natural_gradients * J_inv;
    const double volume = std::abs(det_j) / 6.0;
    return material.k * volume * global_gradients * global_gradients.transpose();
}

Eigen::Vector3d Tet4Element::getTemperatureGradient(
    const std::vector<Node>& nodes,
    const Eigen::VectorXd& nodal_temperatures
) const {
    if (nodal_temperatures.size() != 4) {
        throw std::runtime_error("Tet4 节点温度向量长度必须为 4。");
    }
    const Eigen::Matrix3d J = buildJacobian(nodes);
    const double det_j = J.determinant();
    if (std::abs(det_j) <= std::numeric_limits<double>::epsilon()) {
        throw std::runtime_error("Tet4 温度梯度计算失败：雅可比行列式接近 0。");
    }

    Eigen::Matrix<double, 4, 3> natural_gradients;
    natural_gradients <<
        -1.0, -1.0, -1.0,
         1.0,  0.0,  0.0,
         0.0,  1.0,  0.0,
         0.0,  0.0,  1.0;
    const Eigen::Matrix<double, 4, 3> global_gradients = natural_gradients * J.inverse();
    return global_gradients.transpose() * nodal_temperatures;
}

Eigen::Matrix<double, 6, 12> Tet4Element::buildBMatrix(const std::vector<Node>& nodes) const {
    const Eigen::Matrix3d J = buildJacobian(nodes);
    const double det_j = J.determinant();

    if (std::abs(det_j) <= std::numeric_limits<double>::epsilon()) {
        std::ostringstream oss;
        oss << "Tet4 单元 " << id << " 的雅可比行列式接近 0，无法构造 B 矩阵。";
        throw std::runtime_error(oss.str());
    }

    const Eigen::Matrix3d J_inv = J.inverse();

    // 四面体线性形函数对局部坐标 (r, s, t) 的导数。
    Eigen::Matrix<double, 4, 3> natural_gradients;
    natural_gradients <<
        -1.0, -1.0, -1.0,
         1.0,  0.0,  0.0,
         0.0,  1.0,  0.0,
         0.0,  0.0,  1.0;

    // 每一行对应一个形函数对全局坐标 (x, y, z) 的导数。
    const Eigen::Matrix<double, 4, 3> global_gradients = natural_gradients * J_inv;

    Eigen::Matrix<double, 6, 12> B = Eigen::Matrix<double, 6, 12>::Zero();
    for (int i = 0; i < 4; ++i) {
        const int col = i * 3;
        const double dndx = global_gradients(i, 0);
        const double dndy = global_gradients(i, 1);
        const double dndz = global_gradients(i, 2);

        B(0, col) = dndx;
        B(1, col + 1) = dndy;
        B(2, col + 2) = dndz;

        B(3, col) = dndy;
        B(3, col + 1) = dndx;

        B(4, col + 1) = dndz;
        B(4, col + 2) = dndy;

        B(5, col) = dndz;
        B(5, col + 2) = dndx;
    }

    return B;
}

Eigen::MatrixXd Tet4Element::getStiffnessMatrix(
    const std::vector<Node>& nodes,
    const Material& material
) const {
    const Eigen::Matrix<double, 6, 12> B = buildBMatrix(nodes);
    const Eigen::Matrix<double, 6, 6> D = material.getElasticityMatrix();
    const double volume = computeVolume(nodes);

    return volume * B.transpose() * D * B;
}

Eigen::VectorXd Tet4Element::getStrain(
    const std::vector<Node>& nodes,
    const Eigen::VectorXd& element_displacements
) const {
    if (element_displacements.size() != 12) {
        throw std::runtime_error("Tet4 单元位移向量长度必须为 12。");
    }

    const Eigen::Matrix<double, 6, 12> B = buildBMatrix(nodes);
    return B * element_displacements;
}

Eigen::VectorXd Tet4Element::getStress(
    const std::vector<Node>& nodes,
    const Material& material,
    const Eigen::VectorXd& element_displacements
) const {
    return material.getStressFromStrain(getStrain(nodes, element_displacements));
}

Eigen::MatrixXd Tet4Element::getTangentStiffnessMatrix(
    const std::vector<Node>& nodes,
    const Material& material,
    const Eigen::VectorXd& element_displacements
) const {
    const Eigen::Matrix<double, 6, 12> B = buildBMatrix(nodes);
    const Eigen::Matrix<double, 6, 6> D = material.getTangentMatrix(getStrain(nodes, element_displacements));
    const double volume = computeVolume(nodes);
    return volume * B.transpose() * D * B;
}

Eigen::VectorXd Tet4Element::getInternalForce(
    const std::vector<Node>& nodes,
    const Material& material,
    const Eigen::VectorXd& element_displacements
) const {
    const Eigen::Matrix<double, 6, 12> B = buildBMatrix(nodes);
    const Eigen::VectorXd stress = getStress(nodes, material, element_displacements);
    const double volume = computeVolume(nodes);
    return volume * B.transpose() * stress;
}

// ------------------------------------------------------------
// Hex8Element
// ------------------------------------------------------------

Hex8Element::Hex8Element(int element_id, const std::vector<int>& element_node_ids, int element_material_id) {
    if (element_node_ids.size() != 8) {
        throw std::runtime_error("Hex8 单元必须恰好包含 8 个节点编号。");
    }

    id = element_id;
    node_ids = element_node_ids;
    material_id = element_material_id;
}

void Hex8Element::validateNodeAccess(const std::vector<Node>& nodes) const {
    for (const int node_id : node_ids) {
        if (node_id < 0 || node_id >= static_cast<int>(nodes.size())) {
            std::ostringstream oss;
            oss << "Hex8 单元 " << id << " 引用了不存在的节点编号 " << node_id << "。";
            throw std::runtime_error(oss.str());
        }
    }
}

Eigen::Matrix<double, 8, 3> Hex8Element::buildNaturalGradients(double xi, double eta, double zeta) const {
    Eigen::Matrix<double, 8, 3> gradients;
    gradients <<
        -(1.0 - eta) * (1.0 - zeta), -(1.0 - xi) * (1.0 - zeta), -(1.0 - xi) * (1.0 - eta),
         (1.0 - eta) * (1.0 - zeta), -(1.0 + xi) * (1.0 - zeta), -(1.0 + xi) * (1.0 - eta),
         (1.0 + eta) * (1.0 - zeta),  (1.0 + xi) * (1.0 - zeta), -(1.0 + xi) * (1.0 + eta),
        -(1.0 + eta) * (1.0 - zeta),  (1.0 - xi) * (1.0 - zeta), -(1.0 - xi) * (1.0 + eta),
        -(1.0 - eta) * (1.0 + zeta), -(1.0 - xi) * (1.0 + zeta),  (1.0 - xi) * (1.0 - eta),
         (1.0 - eta) * (1.0 + zeta), -(1.0 + xi) * (1.0 + zeta),  (1.0 + xi) * (1.0 - eta),
         (1.0 + eta) * (1.0 + zeta),  (1.0 + xi) * (1.0 + zeta),  (1.0 + xi) * (1.0 + eta),
        -(1.0 + eta) * (1.0 + zeta),  (1.0 - xi) * (1.0 + zeta),  (1.0 - xi) * (1.0 + eta);
    gradients *= 0.125;
    return gradients;
}

Eigen::Matrix3d Hex8Element::buildJacobian(
    const std::vector<Node>& nodes,
    const Eigen::Matrix<double, 8, 3>& natural_gradients
) const {
    validateNodeAccess(nodes);

    Eigen::Matrix3d J = Eigen::Matrix3d::Zero();
    for (int i = 0; i < 8; ++i) {
        const Eigen::Vector3d& coords = nodes[node_ids[i]].coords;
        J(0, 0) += natural_gradients(i, 0) * coords(0);
        J(0, 1) += natural_gradients(i, 0) * coords(1);
        J(0, 2) += natural_gradients(i, 0) * coords(2);
        J(1, 0) += natural_gradients(i, 1) * coords(0);
        J(1, 1) += natural_gradients(i, 1) * coords(1);
        J(1, 2) += natural_gradients(i, 1) * coords(2);
        J(2, 0) += natural_gradients(i, 2) * coords(0);
        J(2, 1) += natural_gradients(i, 2) * coords(1);
        J(2, 2) += natural_gradients(i, 2) * coords(2);
    }
    return J;
}

Eigen::Matrix<double, 6, 24> Hex8Element::buildBMatrix(
    const std::vector<Node>& nodes,
    double xi,
    double eta,
    double zeta,
    double& det_j
) const {
    const Eigen::Matrix<double, 8, 3> natural_gradients = buildNaturalGradients(xi, eta, zeta);
    const Eigen::Matrix3d J = buildJacobian(nodes, natural_gradients);
    det_j = J.determinant();

    if (std::abs(det_j) <= std::numeric_limits<double>::epsilon()) {
        std::ostringstream oss;
        oss << "Hex8 单元 " << id << " 的雅可比行列式接近 0，无法构造 B 矩阵。";
        throw std::runtime_error(oss.str());
    }

    const Eigen::Matrix3d J_inv = J.inverse();
    const Eigen::Matrix<double, 8, 3> global_gradients = natural_gradients * J_inv;

    Eigen::Matrix<double, 6, 24> B = Eigen::Matrix<double, 6, 24>::Zero();
    for (int i = 0; i < 8; ++i) {
        const int col = i * 3;
        const double dndx = global_gradients(i, 0);
        const double dndy = global_gradients(i, 1);
        const double dndz = global_gradients(i, 2);

        B(0, col) = dndx;
        B(1, col + 1) = dndy;
        B(2, col + 2) = dndz;
        B(3, col) = dndy;
        B(3, col + 1) = dndx;
        B(4, col + 1) = dndz;
        B(4, col + 2) = dndy;
        B(5, col) = dndz;
        B(5, col + 2) = dndx;
    }
    return B;
}

double Hex8Element::computeVolume(const std::vector<Node>& nodes) const {
    static const double inv_sqrt3 = 1.0 / std::sqrt(3.0);
    static const double gauss_points[2] = {-inv_sqrt3, inv_sqrt3};

    double volume = 0.0;
    for (const double xi : gauss_points) {
        for (const double eta : gauss_points) {
            for (const double zeta : gauss_points) {
                const Eigen::Matrix<double, 8, 3> natural_gradients = buildNaturalGradients(xi, eta, zeta);
                const Eigen::Matrix3d J = buildJacobian(nodes, natural_gradients);
                const double det_j = J.determinant();
                if (det_j <= std::numeric_limits<double>::epsilon()) {
                    std::ostringstream oss;
                    oss << "Hex8 单元 " << id << " 的雅可比行列式不为正，网格质量异常。";
                    throw std::runtime_error(oss.str());
                }
                volume += det_j;
            }
        }
    }
    return volume;
}

Eigen::MatrixXd Hex8Element::getThermalConductivityMatrix(
    const std::vector<Node>& nodes,
    const Material& material
) const {
    if (material.k <= 0.0) {
        throw std::runtime_error("材料导热系数必须大于 0。");
    }

    static const double inv_sqrt3 = 1.0 / std::sqrt(3.0);
    static const double gauss_points[2] = {-inv_sqrt3, inv_sqrt3};
    Eigen::MatrixXd Kt = Eigen::MatrixXd::Zero(8, 8);

    for (const double xi : gauss_points) {
        for (const double eta : gauss_points) {
            for (const double zeta : gauss_points) {
                const Eigen::Matrix<double, 8, 3> natural_gradients = buildNaturalGradients(xi, eta, zeta);
                const Eigen::Matrix3d J = buildJacobian(nodes, natural_gradients);
                const double det_j = J.determinant();
                if (std::abs(det_j) <= std::numeric_limits<double>::epsilon()) {
                    throw std::runtime_error("Hex8 热传导矩阵组装失败：雅可比行列式接近 0。");
                }
                const Eigen::Matrix<double, 8, 3> global_gradients = natural_gradients * J.inverse();
                Kt += material.k * global_gradients * global_gradients.transpose() * det_j;
            }
        }
    }
    return Kt;
}

Eigen::Vector3d Hex8Element::getTemperatureGradient(
    const std::vector<Node>& nodes,
    const Eigen::VectorXd& nodal_temperatures
) const {
    if (nodal_temperatures.size() != 8) {
        throw std::runtime_error("Hex8 节点温度向量长度必须为 8。");
    }
    const Eigen::Matrix<double, 8, 3> natural_gradients = buildNaturalGradients(0.0, 0.0, 0.0);
    const Eigen::Matrix3d J = buildJacobian(nodes, natural_gradients);
    const double det_j = J.determinant();
    if (std::abs(det_j) <= std::numeric_limits<double>::epsilon()) {
        throw std::runtime_error("Hex8 温度梯度计算失败：雅可比行列式接近 0。");
    }
    const Eigen::Matrix<double, 8, 3> global_gradients = natural_gradients * J.inverse();
    return global_gradients.transpose() * nodal_temperatures;
}

Eigen::MatrixXd Hex8Element::getStiffnessMatrix(
    const std::vector<Node>& nodes,
    const Material& material
) const {
    return getTangentStiffnessMatrix(nodes, material, Eigen::VectorXd::Zero(24));
}

Eigen::VectorXd Hex8Element::getStrain(
    const std::vector<Node>& nodes,
    const Eigen::VectorXd& element_displacements
) const {
    if (element_displacements.size() != 24) {
        throw std::runtime_error("Hex8 单元位移向量长度必须为 24。");
    }

    double det_j = 0.0;
    const Eigen::Matrix<double, 6, 24> B = buildBMatrix(nodes, 0.0, 0.0, 0.0, det_j);
    return B * element_displacements;
}

Eigen::VectorXd Hex8Element::getStress(
    const std::vector<Node>& nodes,
    const Material& material,
    const Eigen::VectorXd& element_displacements
) const {
    return material.getStressFromStrain(getStrain(nodes, element_displacements));
}

Eigen::MatrixXd Hex8Element::getTangentStiffnessMatrix(
    const std::vector<Node>& nodes,
    const Material& material,
    const Eigen::VectorXd& element_displacements
) const {
    static const double inv_sqrt3 = 1.0 / std::sqrt(3.0);
    static const double gauss_points[2] = {-inv_sqrt3, inv_sqrt3};

    Eigen::MatrixXd K = Eigen::MatrixXd::Zero(24, 24);
    for (const double xi : gauss_points) {
        for (const double eta : gauss_points) {
            for (const double zeta : gauss_points) {
                double det_j = 0.0;
                const Eigen::Matrix<double, 6, 24> B = buildBMatrix(nodes, xi, eta, zeta, det_j);
                const Eigen::VectorXd strain = B * element_displacements;
                const Eigen::Matrix<double, 6, 6> D = material.getTangentMatrix(strain);
                K += B.transpose() * D * B * det_j;
            }
        }
    }
    return K;
}

Eigen::VectorXd Hex8Element::getInternalForce(
    const std::vector<Node>& nodes,
    const Material& material,
    const Eigen::VectorXd& element_displacements
) const {
    static const double inv_sqrt3 = 1.0 / std::sqrt(3.0);
    static const double gauss_points[2] = {-inv_sqrt3, inv_sqrt3};

    Eigen::VectorXd internal_force = Eigen::VectorXd::Zero(24);
    for (const double xi : gauss_points) {
        for (const double eta : gauss_points) {
            for (const double zeta : gauss_points) {
                double det_j = 0.0;
                const Eigen::Matrix<double, 6, 24> B = buildBMatrix(nodes, xi, eta, zeta, det_j);
                const Eigen::VectorXd strain = B * element_displacements;
                const Eigen::VectorXd stress = material.getStressFromStrain(strain);
                internal_force += B.transpose() * stress * det_j;
            }
        }
    }
    return internal_force;
}

// ------------------------------------------------------------
// BoundaryCondition / Load
// ------------------------------------------------------------

BoundaryCondition::BoundaryCondition(int constrained_node_id, int constrained_dof, double constrained_value)
    : node_id(constrained_node_id), dof(constrained_dof), value(constrained_value) {}

Load::Load(int loaded_node_id, double fx, double fy, double fz)
    : node_id(loaded_node_id), force(fx, fy, fz) {}

ThermalBoundaryCondition::ThermalBoundaryCondition(int constrained_node_id, double constrained_temperature)
    : node_id(constrained_node_id), temperature(constrained_temperature) {}

ThermalLoad::ThermalLoad(int loaded_node_id, double thermal_value)
    : node_id(loaded_node_id), value(thermal_value) {}

// ------------------------------------------------------------
// FEMSolver
// ------------------------------------------------------------

FEMSolver::FEMSolver()
    : num_dofs(0),
      num_thermal_dofs(0),
      parallel_enabled(true),
      num_threads(0),
      linear_solver_type("sparse_lu"),
      last_converged(false),
      last_iteration_count(0),
      last_residual_norm(0.0) {}

void FEMSolver::validateNodeId(int node_id, const std::string& scene) const {
    if (node_id < 0 || node_id >= static_cast<int>(nodes.size())) {
        std::ostringstream oss;
        oss << scene << "时引用了不存在的节点编号 " << node_id << "。";
        throw std::runtime_error(oss.str());
    }
}

const Material& FEMSolver::getMaterialById(int material_id) const {
    const auto iter = std::find_if(
        materials.begin(),
        materials.end(),
        [material_id](const Material& material) { return material.id == material_id; }
    );

    if (iter == materials.end()) {
        std::ostringstream oss;
        oss << "未找到编号为 " << material_id << " 的材料。";
        throw std::runtime_error(oss.str());
    }

    return *iter;
}

Eigen::VectorXd FEMSolver::buildElementDisplacementVector(const Element& element) const {
    Eigen::VectorXd element_u = Eigen::VectorXd::Zero(static_cast<int>(element.node_ids.size()) * 3);

    for (int i = 0; i < static_cast<int>(element.node_ids.size()); ++i) {
        const int node_id = element.node_ids[i];
        validateNodeId(node_id, "提取单元位移");
        element_u.segment<3>(i * 3) = nodes[node_id].displacement;
    }

    return element_u;
}

Eigen::VectorXd FEMSolver::buildElementTemperatureVector(const Element& element) const {
    Eigen::VectorXd element_t = Eigen::VectorXd::Zero(static_cast<int>(element.node_ids.size()));
    for (int i = 0; i < static_cast<int>(element.node_ids.size()); ++i) {
        const int node_id = element.node_ids[i];
        validateNodeId(node_id, "提取单元温度");
        if (node_id >= T_global.size()) {
            throw std::runtime_error("热分析节点温度向量尚未初始化。");
        }
        element_t(i) = T_global(node_id);
    }
    return element_t;
}

void FEMSolver::ensureModelReadyForSolution() const {
    if (nodes.empty()) {
        throw std::runtime_error("求解前至少需要 1 个节点。");
    }
    if (elements.empty()) {
        throw std::runtime_error("求解前至少需要 1 个单元。");
    }
    if (materials.empty()) {
        throw std::runtime_error("求解前至少需要 1 个材料。");
    }
}

Eigen::VectorXd FEMSolver::solveLinearSystem(
    const Eigen::SparseMatrix<double>& matrix,
    const Eigen::VectorXd& rhs
) const {
    if (linear_solver_type == "sparse_lu") {
        Eigen::SparseLU<Eigen::SparseMatrix<double>> solver;
        solver.analyzePattern(matrix);
        solver.factorize(matrix);
        if (solver.info() != Eigen::Success) {
            throw std::runtime_error("SparseLU 分解失败，请检查边界条件或网格质量。");
        }

        const Eigen::VectorXd solution = solver.solve(rhs);
        if (solver.info() != Eigen::Success) {
            throw std::runtime_error("SparseLU 求解失败。");
        }
        return solution;
    }

    if (linear_solver_type == "conjugate_gradient") {
        Eigen::ConjugateGradient<Eigen::SparseMatrix<double>, Eigen::Lower | Eigen::Upper> solver;
        solver.compute(matrix);
        if (solver.info() != Eigen::Success) {
            throw std::runtime_error("ConjugateGradient 预处理失败。");
        }

        const Eigen::VectorXd solution = solver.solve(rhs);
        if (solver.info() != Eigen::Success) {
            throw std::runtime_error("ConjugateGradient 求解失败。");
        }
        return solution;
    }

    if (linear_solver_type == "bicgstab") {
        Eigen::BiCGSTAB<Eigen::SparseMatrix<double>> solver;
        solver.compute(matrix);
        if (solver.info() != Eigen::Success) {
            throw std::runtime_error("BiCGSTAB 预处理失败。");
        }

        const Eigen::VectorXd solution = solver.solve(rhs);
        if (solver.info() != Eigen::Success) {
            throw std::runtime_error("BiCGSTAB 求解失败。");
        }
        return solution;
    }

    std::ostringstream oss;
    oss << "不支持的线性求解器类型：" << linear_solver_type
        << "。当前支持：sparse_lu / conjugate_gradient / bicgstab。";
    throw std::runtime_error(oss.str());
}

void FEMSolver::addNode(int id, double x, double y, double z) {
    // 当前求解器为简洁起见，要求节点编号与内部存储顺序一致。
    // 这样 Python 侧传入网格时，节点编号直接对应数组下标，便于初学阶段理解。
    if (id != static_cast<int>(nodes.size())) {
        std::ostringstream oss;
        oss << "当前版本要求节点编号从 0 开始连续递增。期望编号为 "
            << nodes.size() << "，实际收到 " << id << "。";
        throw std::runtime_error(oss.str());
    }

    nodes.emplace_back(id, x, y, z);
}

void FEMSolver::addElement(const std::shared_ptr<Element>& elem) {
    if (!elem) {
        throw std::runtime_error("不能添加空单元指针。");
    }
    elements.push_back(elem);
}

void FEMSolver::addMaterial(const Material& mat) {
    const auto iter = std::find_if(
        materials.begin(),
        materials.end(),
        [&mat](const Material& existing) { return existing.id == mat.id; }
    );

    if (iter != materials.end()) {
        std::ostringstream oss;
        oss << "材料编号 " << mat.id << " 已存在，请保持材料编号唯一。";
        throw std::runtime_error(oss.str());
    }

    materials.push_back(mat);
}

void FEMSolver::addBoundaryCondition(int node_id, int dof, double value) {
    validateNodeId(node_id, "添加边界条件");
    if (dof < 0 || dof > 2) {
        throw std::runtime_error("边界条件自由度 dof 必须是 0、1、2 之一。");
    }
    bcs.emplace_back(node_id, dof, value);
}

void FEMSolver::addLoad(int node_id, double fx, double fy, double fz) {
    validateNodeId(node_id, "添加载荷");
    loads.emplace_back(node_id, fx, fy, fz);
}

void FEMSolver::addThermalBoundaryCondition(int node_id, double temperature) {
    validateNodeId(node_id, "添加热边界条件");
    thermal_bcs.emplace_back(node_id, temperature);
}

void FEMSolver::addThermalLoad(int node_id, double value) {
    validateNodeId(node_id, "添加热载荷");
    thermal_loads.emplace_back(node_id, value);
}

void FEMSolver::initialize() {
    ensureModelReadyForSolution();

    num_dofs = static_cast<int>(nodes.size()) * 3;
    num_thermal_dofs = static_cast<int>(nodes.size());
    K_global.resize(num_dofs, num_dofs);
    M_global.resize(num_dofs, num_dofs);
    K_thermal_global.resize(num_thermal_dofs, num_thermal_dofs);
    K_global.setZero();
    M_global.setZero();
    K_thermal_global.setZero();
    F_global = Eigen::VectorXd::Zero(num_dofs);
    U_global = Eigen::VectorXd::Zero(num_dofs);
    Q_thermal_global = Eigen::VectorXd::Zero(num_thermal_dofs);
    T_global = Eigen::VectorXd::Zero(num_thermal_dofs);
    modal_frequencies_hz.clear();
    modal_shapes.clear();
    transient_times.clear();
    transient_max_displacements.clear();
    transient_loaded_ux_history.clear();
    transient_loaded_uy_history.clear();
    transient_loaded_uz_history.clear();
    transient_loaded_umag_history.clear();
    frequency_response_frequencies_hz.clear();
    frequency_response_max_displacements.clear();
    frequency_response_loaded_ux_history.clear();
    frequency_response_loaded_uy_history.clear();
    frequency_response_loaded_uz_history.clear();
    frequency_response_loaded_umag_history.clear();
    thermal_heat_flux_magnitudes.clear();
}

void FEMSolver::assembleGlobalStiffnessMatrix() {
    ensureModelReadyForSolution();

    using Triplet = Eigen::Triplet<double>;
    std::vector<Triplet> triplets;
    triplets.reserve(elements.size() * 12 * 12);

    const int thread_count = getNumThreads();

#ifdef _OPENMP
    if (parallel_enabled && thread_count > 1) {
        std::vector<std::vector<Triplet>> thread_triplets(static_cast<std::size_t>(thread_count));

#pragma omp parallel num_threads(thread_count)
        {
            const int tid = omp_get_thread_num();
            auto& local_triplets = thread_triplets[static_cast<std::size_t>(tid)];
            local_triplets.reserve((elements.size() / static_cast<std::size_t>(thread_count) + 1) * 12 * 12);

#pragma omp for schedule(static)
            for (int element_index = 0; element_index < static_cast<int>(elements.size()); ++element_index) {
                const auto& element = elements[static_cast<std::size_t>(element_index)];
                const Material& material = getMaterialById(element->material_id);
                const Eigen::VectorXd element_u = buildElementDisplacementVector(*element);
                const Eigen::MatrixXd K_e = element->getTangentStiffnessMatrix(nodes, material, element_u);
                const int element_dofs = static_cast<int>(element->node_ids.size()) * 3;

                for (int i = 0; i < element_dofs; ++i) {
                    const int global_i = element->node_ids[i / 3] * 3 + (i % 3);
                    for (int j = 0; j < element_dofs; ++j) {
                        const int global_j = element->node_ids[j / 3] * 3 + (j % 3);
                        local_triplets.emplace_back(global_i, global_j, K_e(i, j));
                    }
                }
            }
        }

        for (auto& local_triplets : thread_triplets) {
            triplets.insert(triplets.end(), local_triplets.begin(), local_triplets.end());
        }
    } else
#endif
    {
        for (const auto& element : elements) {
            const Material& material = getMaterialById(element->material_id);
            const Eigen::VectorXd element_u = buildElementDisplacementVector(*element);
            const Eigen::MatrixXd K_e = element->getTangentStiffnessMatrix(nodes, material, element_u);
            const int element_dofs = static_cast<int>(element->node_ids.size()) * 3;

            for (int i = 0; i < element_dofs; ++i) {
                const int global_i = element->node_ids[i / 3] * 3 + (i % 3);
                for (int j = 0; j < element_dofs; ++j) {
                    const int global_j = element->node_ids[j / 3] * 3 + (j % 3);
                    triplets.emplace_back(global_i, global_j, K_e(i, j));
                }
            }
        }
    }

    K_global.setZero();
    K_global.setFromTriplets(
        triplets.begin(),
        triplets.end(),
        [](double lhs, double rhs) { return lhs + rhs; }
    );
    K_global.makeCompressed();
}

void FEMSolver::assembleGlobalMassMatrix() {
    ensureModelReadyForSolution();

    using Triplet = Eigen::Triplet<double>;
    std::vector<Triplet> triplets;
    triplets.reserve(elements.size() * 24);
    const int thread_count = getNumThreads();

#ifdef _OPENMP
    if (parallel_enabled && thread_count > 1) {
        std::vector<std::vector<Triplet>> thread_triplets(static_cast<std::size_t>(thread_count));

#pragma omp parallel num_threads(thread_count)
        {
            const int tid = omp_get_thread_num();
            auto& local_triplets = thread_triplets[static_cast<std::size_t>(tid)];
            local_triplets.reserve((elements.size() / static_cast<std::size_t>(thread_count) + 1) * 24);

#pragma omp for schedule(static)
            for (int element_index = 0; element_index < static_cast<int>(elements.size()); ++element_index) {
                const auto& element = elements[static_cast<std::size_t>(element_index)];
                const Material& material = getMaterialById(element->material_id);
                const double volume = element->computeVolume(nodes);
                const int node_count = static_cast<int>(element->node_ids.size());
                if (node_count <= 0) {
                    continue;
                }

                const double nodal_mass = material.rho * volume / static_cast<double>(node_count);
                for (int local_node_index = 0; local_node_index < node_count; ++local_node_index) {
                    const int global_node_id = element->node_ids[local_node_index];
                    for (int axis = 0; axis < 3; ++axis) {
                        const int global_dof = global_node_id * 3 + axis;
                        local_triplets.emplace_back(global_dof, global_dof, nodal_mass);
                    }
                }
            }
        }

        for (auto& local_triplets : thread_triplets) {
            triplets.insert(triplets.end(), local_triplets.begin(), local_triplets.end());
        }
    } else
#endif
    {
        for (const auto& element : elements) {
            const Material& material = getMaterialById(element->material_id);
            const double volume = element->computeVolume(nodes);
            const int node_count = static_cast<int>(element->node_ids.size());
            if (node_count <= 0) {
                continue;
            }

            const double nodal_mass = material.rho * volume / static_cast<double>(node_count);
            for (int local_node_index = 0; local_node_index < node_count; ++local_node_index) {
                const int global_node_id = element->node_ids[local_node_index];
                for (int axis = 0; axis < 3; ++axis) {
                    const int global_dof = global_node_id * 3 + axis;
                    triplets.emplace_back(global_dof, global_dof, nodal_mass);
                }
            }
        }
    }

    M_global.setZero();
    M_global.setFromTriplets(
        triplets.begin(),
        triplets.end(),
        [](double lhs, double rhs) { return lhs + rhs; }
    );
    M_global.makeCompressed();
}

void FEMSolver::assembleGlobalThermalConductivityMatrix() {
    ensureModelReadyForSolution();

    using Triplet = Eigen::Triplet<double>;
    std::vector<Triplet> triplets;
    triplets.reserve(elements.size() * 64);

    for (const auto& element : elements) {
        const Material& material = getMaterialById(element->material_id);
        const Eigen::MatrixXd Kt_e = element->getThermalConductivityMatrix(nodes, material);
        const int element_dofs = static_cast<int>(element->node_ids.size());
        for (int i = 0; i < element_dofs; ++i) {
            const int global_i = element->node_ids[static_cast<std::size_t>(i)];
            for (int j = 0; j < element_dofs; ++j) {
                const int global_j = element->node_ids[static_cast<std::size_t>(j)];
                triplets.emplace_back(global_i, global_j, Kt_e(i, j));
            }
        }
    }

    K_thermal_global.setZero();
    K_thermal_global.setFromTriplets(
        triplets.begin(),
        triplets.end(),
        [](double lhs, double rhs) { return lhs + rhs; }
    );
    K_thermal_global.makeCompressed();
}

void FEMSolver::assembleGlobalThermalLoadVector() {
    if (num_thermal_dofs <= 0) {
        throw std::runtime_error("请先调用 initialize() 初始化热求解器。");
    }
    Q_thermal_global = Eigen::VectorXd::Zero(num_thermal_dofs);
    for (const auto& thermal_load : thermal_loads) {
        Q_thermal_global(thermal_load.node_id) += thermal_load.value;
    }
}

Eigen::VectorXd FEMSolver::assembleGlobalInternalForceVector() const {
    if (num_dofs <= 0) {
        throw std::runtime_error("请先调用 initialize() 初始化求解器。");
    }

    Eigen::VectorXd internal_force = Eigen::VectorXd::Zero(num_dofs);
    for (const auto& element : elements) {
        const Material& material = getMaterialById(element->material_id);
        const Eigen::VectorXd element_u = buildElementDisplacementVector(*element);
        const Eigen::VectorXd internal_force_element = element->getInternalForce(nodes, material, element_u);
        const int element_dofs = static_cast<int>(element->node_ids.size()) * 3;

        for (int i = 0; i < element_dofs; ++i) {
            const int global_i = element->node_ids[i / 3] * 3 + (i % 3);
            internal_force(global_i) += internal_force_element(i);
        }
    }
    return internal_force;
}

void FEMSolver::assembleGlobalForceVector() {
    if (num_dofs <= 0) {
        throw std::runtime_error("请先调用 initialize() 初始化求解器。");
    }

    F_global.setZero();
    for (const auto& load : loads) {
        const int base_dof = load.node_id * 3;
        F_global(base_dof) += load.force(0);
        F_global(base_dof + 1) += load.force(1);
        F_global(base_dof + 2) += load.force(2);
    }
}

void FEMSolver::applyBoundaryConditions() {
    if (K_global.rows() == 0 || K_global.cols() == 0) {
        throw std::runtime_error("请先组装全局刚度矩阵，再施加边界条件。");
    }

    const double max_diagonal = K_global.diagonal().cwiseAbs().maxCoeff();
    const double penalty = std::max(max_diagonal * 1.0e8, 1.0e12);

    for (const auto& bc : bcs) {
        const int global_dof = bc.node_id * 3 + bc.dof;
        K_global.coeffRef(global_dof, global_dof) += penalty;
        F_global(global_dof) += penalty * bc.value;
    }
}

void FEMSolver::applyThermalBoundaryConditions() {
    if (K_thermal_global.rows() == 0 || K_thermal_global.cols() == 0) {
        throw std::runtime_error("请先组装全局热传导矩阵，再施加热边界条件。");
    }

    const double max_diagonal = K_thermal_global.diagonal().cwiseAbs().maxCoeff();
    const double penalty = std::max(max_diagonal * 1.0e8, 1.0e8);

    for (const auto& bc : thermal_bcs) {
        K_thermal_global.coeffRef(bc.node_id, bc.node_id) += penalty;
        Q_thermal_global(bc.node_id) += penalty * bc.temperature;
    }
}

void FEMSolver::solve() {
    if (K_global.rows() == 0 || F_global.size() == 0) {
        throw std::runtime_error("求解前请先完成矩阵和载荷向量装配。");
    }

    U_global = solveLinearSystem(K_global, F_global);

    last_residual_norm = (K_global * U_global - F_global).norm();
    last_iteration_count = 1;
    last_converged = true;
}

void FEMSolver::runSteadyStateThermalAnalysis() {
    initialize();
    assembleGlobalThermalConductivityMatrix();
    assembleGlobalThermalLoadVector();
    applyThermalBoundaryConditions();
    T_global = solveLinearSystem(K_thermal_global, Q_thermal_global);
    last_residual_norm = (K_thermal_global * T_global - Q_thermal_global).norm();
    last_iteration_count = 1;
    last_converged = true;

    thermal_heat_flux_magnitudes.assign(elements.size(), 0.0);
    for (std::size_t element_index = 0; element_index < elements.size(); ++element_index) {
        const auto& element = elements[element_index];
        const Material& material = getMaterialById(element->material_id);
        const Eigen::VectorXd element_t = buildElementTemperatureVector(*element);
        const Eigen::Vector3d gradient = element->getTemperatureGradient(nodes, element_t);
        const Eigen::Vector3d heat_flux = -material.k * gradient;
        thermal_heat_flux_magnitudes[element_index] = heat_flux.norm();
    }
}

void FEMSolver::updateNodalDisplacements() {
    if (U_global.size() != num_dofs) {
        throw std::runtime_error("节点位移更新失败：全局位移向量尚未就绪。");
    }

    for (std::size_t i = 0; i < nodes.size(); ++i) {
        nodes[i].displacement = U_global.segment<3>(static_cast<int>(i) * 3);
    }
}

void FEMSolver::runLinearStaticAnalysis() {
    initialize();
    assembleGlobalStiffnessMatrix();
    assembleGlobalForceVector();
    applyBoundaryConditions();
    solve();
    updateNodalDisplacements();
}

void FEMSolver::runNonlinearStaticAnalysis(int load_steps, int max_iterations, double tolerance) {
    if (load_steps <= 0) {
        throw std::runtime_error("非线性分析的载荷步数必须大于 0。");
    }
    if (max_iterations <= 0) {
        throw std::runtime_error("非线性分析的最大迭代次数必须大于 0。");
    }
    if (tolerance <= 0.0) {
        throw std::runtime_error("非线性分析收敛容差必须大于 0。");
    }

    initialize();
    assembleGlobalForceVector();
    const Eigen::VectorXd full_load = F_global;
    Eigen::VectorXd current_displacement = Eigen::VectorXd::Zero(num_dofs);

    last_converged = false;
    last_iteration_count = 0;
    last_residual_norm = std::numeric_limits<double>::max();

    for (int step = 1; step <= load_steps; ++step) {
        const double load_factor = static_cast<double>(step) / static_cast<double>(load_steps);
        const Eigen::VectorXd target_load = full_load * load_factor;
        last_converged = false;

        for (int iteration = 1; iteration <= max_iterations; ++iteration) {
            U_global = current_displacement;
            updateNodalDisplacements();
            assembleGlobalStiffnessMatrix();

            Eigen::VectorXd residual = target_load - assembleGlobalInternalForceVector();

            const double max_diagonal = K_global.diagonal().cwiseAbs().maxCoeff();
            const double penalty = std::max(max_diagonal * 1.0e8, 1.0e12);
            for (const auto& bc : bcs) {
                const int global_dof = bc.node_id * 3 + bc.dof;
                K_global.coeffRef(global_dof, global_dof) += penalty;
                residual(global_dof) += penalty * (bc.value - current_displacement(global_dof));
            }

            last_residual_norm = residual.norm();
            last_iteration_count = iteration;

            if (last_residual_norm <= tolerance) {
                last_converged = true;
                break;
            }

            const Eigen::VectorXd delta_u = solveLinearSystem(K_global, residual);
            current_displacement += delta_u;
        }

        if (!last_converged) {
            std::ostringstream oss;
            oss << "非线性分析在载荷步 " << step << " 未收敛，残差范数为 " << last_residual_norm << "。";
            throw std::runtime_error(oss.str());
        }
    }

    U_global = current_displacement;
    last_converged = true;
    updateNodalDisplacements();
}

void FEMSolver::runModalAnalysis(int mode_count) {
    if (mode_count <= 0) {
        throw std::runtime_error("模态分析的阶数必须大于 0。");
    }

    initialize();
    assembleGlobalStiffnessMatrix();
    assembleGlobalMassMatrix();

    const std::vector<bool> constrained_mask(static_cast<std::size_t>(num_dofs), false);
    std::vector<bool> dof_is_fixed = constrained_mask;
    for (const auto& bc : bcs) {
        const int global_dof = bc.node_id * 3 + bc.dof;
        if (global_dof >= 0 && global_dof < num_dofs) {
            dof_is_fixed[static_cast<std::size_t>(global_dof)] = true;
        }
    }

    std::vector<int> free_dofs;
    free_dofs.reserve(static_cast<std::size_t>(num_dofs));
    for (int dof = 0; dof < num_dofs; ++dof) {
        if (!dof_is_fixed[static_cast<std::size_t>(dof)]) {
            free_dofs.push_back(dof);
        }
    }

    if (free_dofs.empty()) {
        throw std::runtime_error("模态分析失败：全部自由度都被约束了。");
    }

    Eigen::MatrixXd dense_k = Eigen::MatrixXd::Zero(
        static_cast<int>(free_dofs.size()),
        static_cast<int>(free_dofs.size())
    );
    Eigen::MatrixXd dense_m = Eigen::MatrixXd::Zero(
        static_cast<int>(free_dofs.size()),
        static_cast<int>(free_dofs.size())
    );

    for (int row = 0; row < static_cast<int>(free_dofs.size()); ++row) {
        for (int col = 0; col < static_cast<int>(free_dofs.size()); ++col) {
            dense_k(row, col) = K_global.coeff(free_dofs[static_cast<std::size_t>(row)], free_dofs[static_cast<std::size_t>(col)]);
            dense_m(row, col) = M_global.coeff(free_dofs[static_cast<std::size_t>(row)], free_dofs[static_cast<std::size_t>(col)]);
        }
    }

    for (int index = 0; index < dense_m.rows(); ++index) {
        dense_m(index, index) = std::max(dense_m(index, index), 1.0e-12);
    }

    Eigen::GeneralizedSelfAdjointEigenSolver<Eigen::MatrixXd> eigen_solver(dense_k, dense_m);
    if (eigen_solver.info() != Eigen::Success) {
        throw std::runtime_error("模态分析特征值求解失败。");
    }

    modal_frequencies_hz.clear();
    modal_shapes.clear();
    const Eigen::VectorXd eigenvalues = eigen_solver.eigenvalues();
    const Eigen::MatrixXd eigenvectors = eigen_solver.eigenvectors();

    for (int mode_index = 0; mode_index < eigenvalues.size() && static_cast<int>(modal_frequencies_hz.size()) < mode_count; ++mode_index) {
        const double lambda = eigenvalues(mode_index);
        if (lambda <= 1.0e-12) {
            continue;
        }

        const double omega = std::sqrt(lambda);
        const double pi = std::acos(-1.0);
        const double frequency_hz = omega / (2.0 * pi);
        modal_frequencies_hz.push_back(frequency_hz);

        Eigen::VectorXd full_mode = Eigen::VectorXd::Zero(num_dofs);
        for (int free_index = 0; free_index < static_cast<int>(free_dofs.size()); ++free_index) {
            full_mode(free_dofs[static_cast<std::size_t>(free_index)]) = eigenvectors(free_index, mode_index);
        }

        const double norm = full_mode.norm();
        if (norm > 1.0e-12) {
            full_mode /= norm;
        }
        modal_shapes.push_back(full_mode);
    }

    if (modal_shapes.empty()) {
        throw std::runtime_error("模态分析未提取到有效振型，请检查边界条件与质量参数。");
    }

    U_global = modal_shapes.front();
    updateNodalDisplacements();
    last_converged = true;
    last_iteration_count = static_cast<int>(modal_shapes.size());
    last_residual_norm = 0.0;
}

void FEMSolver::runTransientAnalysis(
    double total_time,
    double time_step,
    double beta,
    double gamma,
    double damping_alpha,
    double damping_beta
) {
    if (total_time <= 0.0) {
        throw std::runtime_error("瞬态分析总时长必须大于 0。");
    }
    if (time_step <= 0.0) {
        throw std::runtime_error("瞬态分析时间步长必须大于 0。");
    }
    if (beta <= 0.0 || gamma <= 0.0) {
        throw std::runtime_error("Newmark 参数 beta 和 gamma 必须大于 0。");
    }

    initialize();
    assembleGlobalStiffnessMatrix();
    assembleGlobalMassMatrix();
    assembleGlobalForceVector();

    Eigen::SparseMatrix<double> C_global = damping_alpha * M_global + damping_beta * K_global;
    Eigen::VectorXd u = Eigen::VectorXd::Zero(num_dofs);
    Eigen::VectorXd v = Eigen::VectorXd::Zero(num_dofs);
    Eigen::VectorXd mass_diagonal = M_global.diagonal();
    for (int index = 0; index < mass_diagonal.size(); ++index) {
        mass_diagonal(index) = std::max(mass_diagonal(index), 1.0e-12);
    }

    Eigen::VectorXd a = (F_global - C_global * v - K_global * u).cwiseQuotient(mass_diagonal);
    const int step_count = std::max(1, static_cast<int>(std::ceil(total_time / time_step)));

    transient_times.clear();
    transient_max_displacements.clear();
    transient_loaded_ux_history.clear();
    transient_loaded_uy_history.clear();
    transient_loaded_uz_history.clear();
    transient_loaded_umag_history.clear();
    transient_times.push_back(0.0);
    transient_max_displacements.push_back(0.0);
    transient_loaded_ux_history.push_back(0.0);
    transient_loaded_uy_history.push_back(0.0);
    transient_loaded_uz_history.push_back(0.0);
    transient_loaded_umag_history.push_back(0.0);

    const double a0 = 1.0 / (beta * time_step * time_step);
    const double a1 = gamma / (beta * time_step);
    const double a2 = 1.0 / (beta * time_step);
    const double a3 = 1.0 / (2.0 * beta) - 1.0;
    const double a4 = gamma / beta - 1.0;
    const double a5 = time_step * (gamma / (2.0 * beta) - 1.0);

    last_converged = true;
    last_residual_norm = 0.0;
    last_iteration_count = step_count;

    for (int step = 1; step <= step_count; ++step) {
        Eigen::SparseMatrix<double> K_effective = K_global + a0 * M_global + a1 * C_global;
        Eigen::VectorXd rhs =
            F_global +
            M_global * (a0 * u + a2 * v + a3 * a) +
            C_global * (a1 * u + a4 * v + a5 * a);

        const double max_diagonal = K_effective.diagonal().cwiseAbs().maxCoeff();
        const double penalty = std::max(max_diagonal * 1.0e8, 1.0e12);
        for (const auto& bc : bcs) {
            const int global_dof = bc.node_id * 3 + bc.dof;
            K_effective.coeffRef(global_dof, global_dof) += penalty;
            rhs(global_dof) += penalty * bc.value;
        }

        const Eigen::VectorXd u_next = solveLinearSystem(K_effective, rhs);
        const Eigen::VectorXd a_next = a0 * (u_next - u) - a2 * v - a3 * a;
        const Eigen::VectorXd v_next = v + time_step * ((1.0 - gamma) * a + gamma * a_next);

        u = u_next;
        v = v_next;
        a = a_next;

        U_global = u;
        updateNodalDisplacements();

        double max_displacement = 0.0;
        for (const auto& node : nodes) {
            max_displacement = std::max(max_displacement, node.displacement.norm());
        }

        double avg_ux = 0.0;
        double avg_uy = 0.0;
        double avg_uz = 0.0;
        double avg_umag = 0.0;
        computeLoadedResponseAverages(loads, nodes, u, avg_ux, avg_uy, avg_uz, avg_umag);

        transient_times.push_back(std::min(step * time_step, total_time));
        transient_max_displacements.push_back(max_displacement);
        transient_loaded_ux_history.push_back(avg_ux);
        transient_loaded_uy_history.push_back(avg_uy);
        transient_loaded_uz_history.push_back(avg_uz);
        transient_loaded_umag_history.push_back(avg_umag);
    }
}

void FEMSolver::runFrequencyResponseAnalysis(
    double start_frequency_hz,
    double end_frequency_hz,
    int point_count,
    double damping_alpha,
    double damping_beta
) {
    if (start_frequency_hz <= 0.0 || end_frequency_hz <= 0.0) {
        throw std::runtime_error("频响分析频率必须大于 0。");
    }
    if (end_frequency_hz < start_frequency_hz) {
        throw std::runtime_error("频响分析终止频率必须大于等于起始频率。");
    }
    if (point_count < 2) {
        throw std::runtime_error("频响分析至少需要 2 个频率点。");
    }

    initialize();
    assembleGlobalStiffnessMatrix();
    assembleGlobalMassMatrix();
    assembleGlobalForceVector();
    Eigen::SparseMatrix<double> C_global = damping_alpha * M_global + damping_beta * K_global;

    std::vector<bool> dof_is_fixed(static_cast<std::size_t>(num_dofs), false);
    for (const auto& bc : bcs) {
        const int global_dof = bc.node_id * 3 + bc.dof;
        if (global_dof >= 0 && global_dof < num_dofs) {
            dof_is_fixed[static_cast<std::size_t>(global_dof)] = true;
        }
    }

    std::vector<int> free_dofs;
    free_dofs.reserve(static_cast<std::size_t>(num_dofs));
    for (int dof = 0; dof < num_dofs; ++dof) {
        if (!dof_is_fixed[static_cast<std::size_t>(dof)]) {
            free_dofs.push_back(dof);
        }
    }
    if (free_dofs.empty()) {
        throw std::runtime_error("频响分析失败：全部自由度都被约束了。");
    }

    Eigen::MatrixXd dense_k = Eigen::MatrixXd::Zero(
        static_cast<int>(free_dofs.size()),
        static_cast<int>(free_dofs.size())
    );
    Eigen::MatrixXd dense_m = Eigen::MatrixXd::Zero(
        static_cast<int>(free_dofs.size()),
        static_cast<int>(free_dofs.size())
    );
    Eigen::MatrixXd dense_c = Eigen::MatrixXd::Zero(
        static_cast<int>(free_dofs.size()),
        static_cast<int>(free_dofs.size())
    );
    Eigen::VectorXcd dense_f = Eigen::VectorXcd::Zero(static_cast<int>(free_dofs.size()));

    for (int row = 0; row < static_cast<int>(free_dofs.size()); ++row) {
        dense_f(row) = std::complex<double>(F_global(free_dofs[static_cast<std::size_t>(row)]), 0.0);
        for (int col = 0; col < static_cast<int>(free_dofs.size()); ++col) {
            dense_k(row, col) = K_global.coeff(free_dofs[static_cast<std::size_t>(row)], free_dofs[static_cast<std::size_t>(col)]);
            dense_m(row, col) = M_global.coeff(free_dofs[static_cast<std::size_t>(row)], free_dofs[static_cast<std::size_t>(col)]);
            dense_c(row, col) = C_global.coeff(free_dofs[static_cast<std::size_t>(row)], free_dofs[static_cast<std::size_t>(col)]);
        }
    }

    frequency_response_frequencies_hz.clear();
    frequency_response_max_displacements.clear();
    frequency_response_loaded_ux_history.clear();
    frequency_response_loaded_uy_history.clear();
    frequency_response_loaded_uz_history.clear();
    frequency_response_loaded_umag_history.clear();
    Eigen::VectorXd best_amplitude = Eigen::VectorXd::Zero(num_dofs);
    double best_peak_value = -1.0;

    const double pi = std::acos(-1.0);
    for (int point_index = 0; point_index < point_count; ++point_index) {
        const double ratio = static_cast<double>(point_index) / static_cast<double>(point_count - 1);
        const double frequency_hz = start_frequency_hz + (end_frequency_hz - start_frequency_hz) * ratio;
        const double omega = 2.0 * pi * frequency_hz;
        const std::complex<double> imag_unit(0.0, 1.0);

        Eigen::MatrixXcd dynamic_stiffness =
            dense_k.cast<std::complex<double>>() +
            imag_unit * omega * dense_c.cast<std::complex<double>>() -
            (omega * omega) * dense_m.cast<std::complex<double>>();

        Eigen::VectorXcd response = dynamic_stiffness.fullPivLu().solve(dense_f);
        Eigen::VectorXd amplitude = Eigen::VectorXd::Zero(num_dofs);
        for (int free_index = 0; free_index < static_cast<int>(free_dofs.size()); ++free_index) {
            amplitude(free_dofs[static_cast<std::size_t>(free_index)]) = std::abs(response(free_index));
        }

        double max_displacement = 0.0;
        for (int node_index = 0; node_index < static_cast<int>(nodes.size()); ++node_index) {
            const Eigen::Vector3d nodal_amplitude = amplitude.segment<3>(node_index * 3);
            max_displacement = std::max(max_displacement, nodal_amplitude.norm());
        }

        frequency_response_frequencies_hz.push_back(frequency_hz);
        frequency_response_max_displacements.push_back(max_displacement);
        double avg_ux = 0.0;
        double avg_uy = 0.0;
        double avg_uz = 0.0;
        double avg_umag = 0.0;
        computeLoadedResponseAverages(loads, nodes, amplitude, avg_ux, avg_uy, avg_uz, avg_umag);
        frequency_response_loaded_ux_history.push_back(avg_ux);
        frequency_response_loaded_uy_history.push_back(avg_uy);
        frequency_response_loaded_uz_history.push_back(avg_uz);
        frequency_response_loaded_umag_history.push_back(avg_umag);

        if (max_displacement > best_peak_value) {
            best_peak_value = max_displacement;
            best_amplitude = amplitude;
        }
    }

    U_global = best_amplitude;
    updateNodalDisplacements();
    last_converged = true;
    last_iteration_count = point_count;
    last_residual_norm = 0.0;
}

void FEMSolver::setLinearSolverType(const std::string& solver_type) {
    if (
        solver_type != "sparse_lu" &&
        solver_type != "conjugate_gradient" &&
        solver_type != "bicgstab"
    ) {
        throw std::runtime_error("线性求解器类型必须是 sparse_lu、conjugate_gradient 或 bicgstab。");
    }

    linear_solver_type = solver_type;
}

std::string FEMSolver::getLinearSolverType() const {
    return linear_solver_type;
}

void FEMSolver::setParallelEnabled(bool enabled) {
    parallel_enabled = enabled;
}

bool FEMSolver::isParallelEnabled() const {
    return parallel_enabled;
}

void FEMSolver::setNumThreads(int threads) {
    if (threads < 0) {
        throw std::runtime_error("线程数不能小于 0。0 表示自动使用系统可用线程数。");
    }
    num_threads = threads;
}

int FEMSolver::getNumThreads() const {
    if (!parallel_enabled) {
        return 1;
    }
    if (num_threads > 0) {
        return num_threads;
    }
    return detectAvailableThreads();
}

int FEMSolver::getAvailableThreads() const {
    return detectAvailableThreads();
}

bool FEMSolver::hasConverged() const {
    return last_converged;
}

int FEMSolver::getLastIterationCount() const {
    return last_iteration_count;
}

double FEMSolver::getLastResidualNorm() const {
    return last_residual_norm;
}

std::vector<double> FEMSolver::getDisplacements() const {
    std::vector<double> displacements;
    displacements.reserve(nodes.size() * 3);

    for (const auto& node : nodes) {
        displacements.push_back(node.displacement(0));
        displacements.push_back(node.displacement(1));
        displacements.push_back(node.displacement(2));
    }

    return displacements;
}

std::vector<double> FEMSolver::getStresses() const {
    std::vector<double> stresses(elements.size(), 0.0);
    const int thread_count = getNumThreads();

#ifdef _OPENMP
    if (parallel_enabled && thread_count > 1) {
#pragma omp parallel for schedule(static) num_threads(thread_count)
        for (int element_index = 0; element_index < static_cast<int>(elements.size()); ++element_index) {
            const auto& element = elements[static_cast<std::size_t>(element_index)];
            const Material& material = getMaterialById(element->material_id);
            const Eigen::VectorXd element_u = buildElementDisplacementVector(*element);
            const Eigen::VectorXd stress = element->getStress(nodes, material, element_u);
            stresses[static_cast<std::size_t>(element_index)] = computeVonMisesStress(stress);
        }
    } else
#endif
    {
        for (std::size_t element_index = 0; element_index < elements.size(); ++element_index) {
            const auto& element = elements[element_index];
            const Material& material = getMaterialById(element->material_id);
            const Eigen::VectorXd element_u = buildElementDisplacementVector(*element);
            const Eigen::VectorXd stress = element->getStress(nodes, material, element_u);
            stresses[element_index] = computeVonMisesStress(stress);
        }
    }

    return stresses;
}

std::vector<double> FEMSolver::getStrains() const {
    std::vector<double> strains(elements.size(), 0.0);
    const int thread_count = getNumThreads();

#ifdef _OPENMP
    if (parallel_enabled && thread_count > 1) {
#pragma omp parallel for schedule(static) num_threads(thread_count)
        for (int element_index = 0; element_index < static_cast<int>(elements.size()); ++element_index) {
            const auto& element = elements[static_cast<std::size_t>(element_index)];
            const Eigen::VectorXd element_u = buildElementDisplacementVector(*element);
            const Eigen::VectorXd strain = element->getStrain(nodes, element_u);
            strains[static_cast<std::size_t>(element_index)] = computeEquivalentStrain(strain);
        }
    } else
#endif
    {
        for (std::size_t element_index = 0; element_index < elements.size(); ++element_index) {
            const auto& element = elements[element_index];
            const Eigen::VectorXd element_u = buildElementDisplacementVector(*element);
            const Eigen::VectorXd strain = element->getStrain(nodes, element_u);
            strains[element_index] = computeEquivalentStrain(strain);
        }
    }

    return strains;
}

std::vector<double> FEMSolver::getMeshBounds() const {
    if (nodes.empty()) {
        return {};
    }

    double xmin = nodes.front().coords(0);
    double xmax = xmin;
    double ymin = nodes.front().coords(1);
    double ymax = ymin;
    double zmin = nodes.front().coords(2);
    double zmax = zmin;

    for (const auto& node : nodes) {
        xmin = std::min(xmin, node.coords(0));
        xmax = std::max(xmax, node.coords(0));
        ymin = std::min(ymin, node.coords(1));
        ymax = std::max(ymax, node.coords(1));
        zmin = std::min(zmin, node.coords(2));
        zmax = std::max(zmax, node.coords(2));
    }

    return {xmin, xmax, ymin, ymax, zmin, zmax};
}

std::vector<double> FEMSolver::getModalFrequenciesHz() const {
    return modal_frequencies_hz;
}

std::vector<double> FEMSolver::getModeShape(int mode_index) const {
    if (mode_index < 0 || mode_index >= static_cast<int>(modal_shapes.size())) {
        throw std::runtime_error("模态阶次索引超出范围。");
    }

    const Eigen::VectorXd& mode_shape = modal_shapes[static_cast<std::size_t>(mode_index)];
    return std::vector<double>(mode_shape.data(), mode_shape.data() + mode_shape.size());
}

std::vector<double> FEMSolver::getTransientTimes() const {
    return transient_times;
}

std::vector<double> FEMSolver::getTransientMaxDisplacements() const {
    return transient_max_displacements;
}

std::vector<double> FEMSolver::getTransientLoadedUxHistory() const {
    return transient_loaded_ux_history;
}

std::vector<double> FEMSolver::getTransientLoadedUyHistory() const {
    return transient_loaded_uy_history;
}

std::vector<double> FEMSolver::getTransientLoadedUzHistory() const {
    return transient_loaded_uz_history;
}

std::vector<double> FEMSolver::getTransientLoadedUmagHistory() const {
    return transient_loaded_umag_history;
}

std::vector<double> FEMSolver::getFrequencyResponseFrequenciesHz() const {
    return frequency_response_frequencies_hz;
}

std::vector<double> FEMSolver::getFrequencyResponseMaxDisplacements() const {
    return frequency_response_max_displacements;
}

std::vector<double> FEMSolver::getFrequencyResponseLoadedUxHistory() const {
    return frequency_response_loaded_ux_history;
}

std::vector<double> FEMSolver::getFrequencyResponseLoadedUyHistory() const {
    return frequency_response_loaded_uy_history;
}

std::vector<double> FEMSolver::getFrequencyResponseLoadedUzHistory() const {
    return frequency_response_loaded_uz_history;
}

std::vector<double> FEMSolver::getFrequencyResponseLoadedUmagHistory() const {
    return frequency_response_loaded_umag_history;
}

std::vector<double> FEMSolver::getTemperatures() const {
    return std::vector<double>(T_global.data(), T_global.data() + T_global.size());
}

std::vector<double> FEMSolver::getHeatFluxMagnitudes() const {
    return thermal_heat_flux_magnitudes;
}

void FEMSolver::exportResults(const std::string& filename) const {
    std::ofstream file(filename);
    if (!file.is_open()) {
        throw std::runtime_error("结果文件无法写入：" + filename);
    }

    file << std::setprecision(12);
    file << "# Node Results\n";
    file << "NodeID,X,Y,Z,Ux,Uy,Uz\n";
    for (const auto& node : nodes) {
        file << node.id << ","
             << node.coords(0) << ","
             << node.coords(1) << ","
             << node.coords(2) << ","
             << node.displacement(0) << ","
             << node.displacement(1) << ","
             << node.displacement(2) << "\n";
    }

    file << "\n# Element Results\n";
    file << "ElementID,MaterialID,Volume,VonMisesStress,EquivalentStrain\n";

    const std::vector<double> stresses = getStresses();
    const std::vector<double> strains = getStrains();
    for (std::size_t i = 0; i < elements.size(); ++i) {
        file << elements[i]->id << ","
             << elements[i]->material_id << ","
             << elements[i]->computeVolume(nodes) << ","
             << stresses[i] << ","
             << strains[i] << "\n";
    }
}

void FEMSolver::printInfo() const {
    std::cout << "\n========== 求解器信息 ==========\n";
    std::cout << "节点数: " << nodes.size() << "\n";
    std::cout << "单元数: " << elements.size() << "\n";
    std::cout << "材料数: " << materials.size() << "\n";
    std::cout << "边界条件数: " << bcs.size() << "\n";
    std::cout << "载荷数: " << loads.size() << "\n";
    std::cout << "总自由度: " << num_dofs << "\n";
    std::cout << "===============================\n";
}

std::size_t FEMSolver::getNodeCount() const {
    return nodes.size();
}

std::size_t FEMSolver::getElementCount() const {
    return elements.size();
}

std::size_t FEMSolver::getMaterialCount() const {
    return materials.size();
}

std::size_t FEMSolver::getBoundaryConditionCount() const {
    return bcs.size();
}

std::size_t FEMSolver::getLoadCount() const {
    return loads.size();
}

int FEMSolver::getNumDofs() const {
    return num_dofs;
}
