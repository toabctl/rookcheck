# Copyright (c) 2019 SUSE LINUX GmbH
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


# This module should take care of deploying kubernetes. There will likely be
# multiple variations of an abstract base class to do so. However, the
# implementation may need to require a certain base OS. For example, skuba
# would require SLE and can raise an exception if that isn't provided.

from abc import ABC, abstractmethod
import os
import subprocess
import kubernetes
import logging

from tests import config


logger = logging.getLogger(__name__)


# TODO(toabctl): Move Deploy and DeploySUSE out of kubernetes_base.py
class Deploy(ABC):
    @abstractmethod
    def install_kubeadm_play(self):
        pass


class DeploySUSE(Deploy):
    basedir = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                           '../../'))

    def copy_needed_files(self):
        # Temporary workaround for mitogen failing to copy files or templates.
        tasks = []

        service_file = os.path.join(self.basedir, 'assets/kubelet.service')
        tasks.append(
            dict(
                name="Copy kubelet systemd service",
                action=dict(
                    module='copy',
                    args=dict(
                        src=service_file,
                        dest="/usr/lib/systemd/system/"
                    )
                )
            )
        )

        extra_args_file = os.path.join(
            self.basedir, 'assets/KUBELET_EXTRA_ARGS.j2')
        tasks.append(
            dict(
                name="Copy config files",
                action=dict(
                    module='template',
                    args=dict(
                        src=extra_args_file,
                        dest="/root/KUBELET_EXTRA_ARGS"
                    )
                )
            )
        )

        play_source = dict(
            name="Copy needed files",
            hosts="all",
            tasks=tasks,
            gather_facts="no",
            strategy="free",
        )
        return play_source

    def install_kubeadm_play(self):
        tasks = []

        tasks.append(
            dict(
                name="Start required IPVS kernel modules",
                action=dict(
                    module='shell',
                    args=dict(
                        cmd="modprobe ip_vs ; modprobe ip_vs_rr ; "
                            "modprobe ip_vs_wrr ; modprobe ip_vs_sh",
                    )
                )
            )
        )

        grouped_commands = [
            ("wget https://github.com/kubernetes-sigs/cri-tools/releases/"
                "download/{CRICTL_VERSION}/"
                "crictl-{CRICTL_VERSION}-linux-amd64.tar.gz"),
            "tar -C /usr/bin -xf crictl-{CRICTL_VERSION}-linux-amd64.tar.gz",
            "chmod +x /usr/bin/crictl",
            "rm crictl-{CRICTL_VERSION}-linux-amd64.tar.gz",
        ]
        tasks.append(
            dict(
                name="Download and install crictl",
                action=dict(
                    module='shell',
                    args=dict(
                        cmd=" && ".join(grouped_commands).format(
                            CRICTL_VERSION=config.CRICTL_VERSION
                        )
                    )
                )
            )
        )

        for binary in ['kubeadm', 'kubectl', 'kubelet']:
            grouped_commands = [
                ("curl -LO https://storage.googleapis.com/kubernetes-release/"
                    "release/{K8S_VERSION}/bin/linux/amd64/{binary}"),
                "chmod +x {binary}",
                "mv {binary} /usr/bin/"
            ]
            tasks.append(
                dict(
                    name="Download and install %s" % binary,
                    action=dict(
                        module='shell',
                        args=dict(
                            cmd=" && ".join(grouped_commands).format(
                                K8S_VERSION=config.K8S_VERSION,
                                CRICTL_VERSION=config.CRICTL_VERSION,
                                binary=binary
                            )
                        )
                    )
                )
            )

        # CNI plugins are required for most network addons
        # https://github.com/containernetworking/plugins/releases
        CNI_VERSION = "v0.7.5"
        grouped_commands = [
            "rm -f cni-plugins-amd64-{CNI_VERSION}.tgz*",
            ("wget https://github.com/containernetworking/plugins/releases/"
                "download/{CNI_VERSION}/cni-plugins-amd64-{CNI_VERSION}.tgz"),
            "mkdir -p /opt/cni/bin",
            "tar -C /opt/cni/bin -xf cni-plugins-amd64-{CNI_VERSION}.tgz",
            "rm cni-plugins-amd64-{CNI_VERSION}.tgz"
        ]
        tasks.append(
            dict(
                name="Download and install CNI plugins",
                action=dict(
                    module='shell',
                    args=dict(
                        cmd=" && ".join(grouped_commands).format(
                            CNI_VERSION=CNI_VERSION,
                            CRICTL_VERSION=config.CRICTL_VERSION
                        )
                    )
                )
            )
        )

        tasks.append(
            dict(
                name="Enable kubelet service",
                action=dict(
                    module='shell',
                    args=dict(
                        cmd="systemctl enable kubelet"
                    )
                )
            )
        )

        tasks.append(
            dict(
                name="Disable apparmor",
                action=dict(
                    module='shell',
                    args=dict(
                        cmd="systemctl disable apparmor --now || true"
                    )
                )
            )
        )

        play_source = dict(
            name="Install kubeadm",
            hosts="all",
            tasks=tasks,
            gather_facts="no",
            strategy="mitogen_free",
        )
        return play_source

    def copy_needed_files_master(self):
        # Temporary workaround for mitogen failing to copy files or templates.
        tasks = []

        tasks.append(
            dict(
                name="Create /root/.setup-kube dir",
                action=dict(
                    module='file',
                    args=dict(
                        path="/root/.setup-kube",
                        state="directory"
                    )
                )
            )
        )

        cluster_psp_file = os.path.join(
            self.basedir, 'assets/cluster-psp.yaml')
        tasks.append(
            dict(
                name="Copy cluster-psp.yaml",
                action=dict(
                    module='copy',
                    args=dict(
                        src=cluster_psp_file,
                        dest="/root/.setup-kube/"
                    )
                )
            )
        )

        kubeadm_init_file = os.path.join(
            self.basedir, 'assets/kubeadm-init-config.yaml.j2')
        tasks.append(
            dict(
                name="Copy kubeadm-init-config.yaml",
                action=dict(
                    module='template',
                    args=dict(
                        src=kubeadm_init_file,
                        dest="/root/.setup-kube/kubeadm-init-config.yaml"
                    )
                )
            )
        )

        play_source = dict(
            name="Copy needed files for master",
            hosts="first_master",
            tasks=tasks,
            gather_facts="no",
            # Only one host, so more helpful to see current step with linear
            # strategy
            strategy="linear",
        )
        return play_source

    def setup_master_play(self):
        tasks = []

        # init config file has extra API server args to enable psp access
        # control
        init_command = (
            "kubeadm init "
            "--config=/root/.setup-kube/kubeadm-init-config.yaml"
        )
        tasks.append(
            dict(
                name="Run 'kubeadm init'",
                action=dict(
                    module='shell',
                    args=dict(
                        # for idempotency, do not run init if docker is already
                        # running kube resources
                        cmd=("if ! docker ps -a | grep -q kube; "
                             "then %s ; fi" % init_command)
                    )
                )
            )
        )

        grouped_commands = [
            "mkdir -p /root/.kube",
            "ln -f -s /etc/kubernetes/admin.conf /root/.kube/config",
            "kubectl completion bash > ~/.kube/kubectl-completion.sh",
            "chmod +x ~/.kube/kubectl-completion.sh",
        ]
        tasks.append(
            dict(
                name="Set up root user as Kubernetes administrator",
                action=dict(
                    module='shell',
                    args=dict(
                        cmd=" && ".join(grouped_commands)
                    )
                )
            )
        )

        tasks.append(
            dict(
                name="Wait until kubernetes is ready",
                action=dict(
                    module='command',
                    args=dict(
                        cmd="kubectl get nodes",
                    )
                ),
                retries=20,
                delay=5,
                register="cmd_result",
                until="cmd_result.rc == 0",
            )
        )

        tasks.append(
            dict(
                name="Set up default cluster pod security policies (PSPs)",
                action=dict(
                    module='shell',
                    args=dict(
                        cmd=("kubectl apply "
                             "-f /root/.setup-kube/cluster-psp.yaml")
                    )
                )
            )
        )

        # print("setup cluster overlay network CNI")
        # tasks.append(
        #     dict(
        #         action=dict(
        #             module='shell',
        #             args=dict(
        #                 cmd=("kubectl apply -f "
        #                      "https://docs.projectcalico.org/"
        #                      "manifests/calico.yaml")
        #             )
        #         )
        #     )
        # )

        # print("setup cluster overlay network CNI")
        # tasks.append(
        #     dict(
        #         action=dict(
        #             module='shell',
        #             args=dict(
        #                 cmd=("kubectl apply -f "
        #                      "https://raw.githubusercontent.com/coreos/"
        #                      "flannel/master/Documentation/kube-flannel.yml")
        #             )
        #         )
        #     )
        # )

        tasks.append(
            dict(
                name="Get join command",
                action=dict(
                    module='shell',
                    args=dict(
                        cmd=("kubeadm token create --print-join-command "
                             "| grep 'kubeadm join'")
                    )
                )
            )
        )

        play_source = dict(
            name="Set up master",
            hosts="first_master",
            tasks=tasks,
            gather_facts="no",
            # Only one host, so more helpful to see current step with linear
            # strategy
            strategy="mitogen_linear",
        )
        return play_source

    def join_workers_to_master(self, join_command):
        tasks = []

        tasks.append(
            dict(
                name="Join node to Kubernetes cluster",
                action=dict(
                    module='shell',
                    args=dict(
                        # for idempotency, do not run join if docker is already
                        # running kube resources
                        cmd=("if ! docker ps -a | grep -q kube; "
                             "then %s ; fi" % join_command)
                    )
                )
            )
        )

        play_source = dict(
            name="Enroll workers",
            hosts="worker",
            tasks=tasks,
            gather_facts="no",
            strategy="mitogen_free",
        )
        return play_source

    def fetch_kubeconfig(self, destination):
        tasks = []

        tasks.append(
            dict(
                name="Download kubeconfig",
                action=dict(
                    module='fetch',
                    args=dict(
                        src="/root/.kube/config",
                        dest="%s/kubeconfig" % destination,
                        flat=True
                    )
                )
            )
        )

        play_source = dict(
            name="Download kubeconfig",
            hosts="first_master",
            tasks=tasks,
            gather_facts="no",
            # Only one host, so more helpful to see current step with linear
            # strategy
            strategy="mitogen_linear",
        )
        return play_source


class KubernetesBase(ABC):
    def __init__(self, hardware):
        self._hardware = hardware
        # TODO(toabctl): Make it configurable?
        self._kubeconfig = os.path.join(self.hardware.working_dir,
                                        'kubeconfig')
        self._kubectl_exec = os.path.join(self.hardware.working_dir, 'kubectl')
        self.v1 = None
        logger.info(f"kube init on hardware {self.hardware}")
        if config.DISTRO == 'openSUSE_k8s':
            self.distro = DeploySUSE()
        else:
            raise Exception("OS yet to be implemented/unsupport.")

    @abstractmethod
    def install_kubernetes(self):
        pass

    @property
    def hardware(self):
        return self._hardware

    @property
    def kubeconfig(self):
        return self._kubeconfig

    @property
    def kubectl_exec(self):
        return self._kubectl_exec

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.destroy()

    def _configure_kubernetes_client(self):
        kubernetes.config.load_kube_config(self.kubeconfig)
        self.v1 = kubernetes.client.CoreV1Api()

    def kubectl(self, command):
        """
        Run a kubectl command
        """
        try:
            out = subprocess.run(
                "%s --kubeconfig %s %s"
                % (self.kubectl_exec, self.kubeconfig, command),
                shell=True, check=True, universal_newlines=True,
                capture_output=True
            )
        except subprocess.CalledProcessError as e:
            logger.exception(f"Command `{command}` failed")
            logger.error(f"STDOUT: {e.stdout}")
            logger.error(f"STDERR: {e.stderr}")
            raise
        return out

    def kubectl_apply(self, yaml_file):
        return self.kubectl("apply -f %s" % yaml_file)

    def untaint_master(self):
        self.kubectl("taint nodes --all node-role.kubernetes.io/master-")

    def execute_in_pod(self, command, pod, namespace="rook-ceph"):
        return self.kubectl(
            '--namespace %s exec -t "%s" -- bash -c "$(cat <<\'EOF\'\n'
            '%s'
            '\nEOF\n)"'
            % (namespace, pod, command)
        )

    def get_pod_by_app_label(self, label, namespace="rook-ceph"):
        return self.kubectl(
            '--namespace %s get pod -l app="%s"'
            ' --output custom-columns=name:metadata.name --no-headers'
            % (namespace, label)
        ).stdout.strip()

    def execute_in_pod_by_label(self, command, label, namespace="rook-ceph"):
        # Note(jhesketh): The pod isn't cached, so if running multiple commands
        #                 in the one pod consider calling the following
        #                 manually
        pod = self.get_pod_by_app_label(label, namespace)
        return self.execute_in_pod(command, pod, namespace)

    def destroy(self, skip=True):
        logger.info(f"kube destroy on hardware {self.hardware}")
        if skip:
            # We can skip in most cases since the nodes themselves will be
            # destroyed instead.
            return
        # TODO(jhesketh): Uninstall kubernetes
        pass

    def configure_kubernetes_client(self):
        kubernetes.config.load_kube_config(self.kubeconfig)
        self.v1 = kubernetes.client.CoreV1Api()
