#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <utility>
#include <vector>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

// YOLO classification already supplies probabilities. Do not apply softmax again.
std::vector<std::pair<std::size_t, double>> rank_probabilities(
    const std::vector<double>& probabilities, int top_k) {
    if (top_k < 1) {
        throw std::invalid_argument("top_k must be positive");
    }
    if (probabilities.empty()) {
        throw std::invalid_argument("probabilities cannot be empty");
    }
    std::vector<std::pair<std::size_t, double>> ranked;
    ranked.reserve(probabilities.size());
    for (std::size_t index = 0; index < probabilities.size(); ++index) {
        const double probability = probabilities[index];
        if (!std::isfinite(probability) || probability < 0.0 || probability > 1.0) {
            throw std::invalid_argument("probabilities must be finite numbers in [0, 1]");
        }
        ranked.emplace_back(index, probability);
    }
    const auto count = std::min(ranked.size(), static_cast<std::size_t>(top_k));
    std::partial_sort(ranked.begin(), ranked.begin() + count, ranked.end(),
        [](const auto& left, const auto& right) {
            return left.second == right.second ? left.first < right.first : left.second > right.second;
        });
    ranked.resize(count);
    return ranked;
}

PYBIND11_MODULE(_native, module) {
    module.doc() = "C++ Top-K postprocessing for plant disease classification";
    module.def("rank_probabilities", &rank_probabilities,
               pybind11::arg("probabilities"), pybind11::arg("top_k"),
               pybind11::call_guard<pybind11::gil_scoped_release>());
}
