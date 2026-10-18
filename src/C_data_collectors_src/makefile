# From: /work2/05392/cylu/share/remora/binaries/C_collectors_070723
CC = gcc
CXXFLAGS=-O3

OBJECTS += data_collect_io.o data_collect_cpu.o data_collect_eth.o data_collect_ib.o data_collect_memory.o data_collect_numa.o data_collect_opa.o data_collect_power.o data_collect_temperature.o

BINARIES += data_collect_io data_collect_cpu data_collect_eth data_collect_ib data_collect_memory data_collect_numa data_collect_opa data_collect_power data_collect_temperature

ifeq ($(shell ldconfig -p | grep libnvidia-ml >& /dev/null && echo 1 || echo 0), 1)
OBJECTS  += data_collect_gpu.o
BINARIES += data_collect_gpu
endif

BUILD_DIR=../binary_data_collectors

all: $(BINARIES)
	@make clean

data_collect_io: data_collect_io.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) data_collect_io.o -o $(BUILD_DIR)/data_collect_io
	$(info [CC] Building data_collect_io)

data_collect_cpu: data_collect_cpu.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) data_collect_cpu.o -o $(BUILD_DIR)/data_collect_cpu
	$(info [CC] Building data_collect_cpu)

data_collect_eth: data_collect_eth.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) data_collect_eth.o -o $(BUILD_DIR)/data_collect_eth
	$(info [CC] Building data_collect_eth)

data_collect_ib: data_collect_ib.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) data_collect_ib.o -o $(BUILD_DIR)/data_collect_ib
	$(info [CC] Building data_collect_ib)

data_collect_memory: data_collect_memory.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) data_collect_memory.o -o $(BUILD_DIR)/data_collect_memory
	$(info [CC] Building data_collect_memory)

data_collect_numa: data_collect_numa.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) data_collect_numa.o -o $(BUILD_DIR)/data_collect_numa
	$(info [CC] Building data_collect_numa)

data_collect_opa: data_collect_opa.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) data_collect_opa.o -o $(BUILD_DIR)/data_collect_opa
	$(info [CC] Building data_collect_opa)

data_collect_power: data_collect_power.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) data_collect_power.o -o $(BUILD_DIR)/data_collect_power
	$(info [CC] Building data_collect_power)

data_collect_temperature: data_collect_temperature.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) data_collect_temperature.o -o $(BUILD_DIR)/data_collect_temperature
	$(info [CC] Building data_collect_temperature)

data_collect_gpu: data_collect_gpu.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) data_collect_gpu.o -L/lib64 -lnvidia-ml -o $(BUILD_DIR)/data_collect_gpu
	$(info [CC] Building data_collect_gpu)

data_collect_gpu.o: data_collect_gpu.c
	@$(CC) $(CCFLAGS) $(DEPFLAGS) -L/lib64 -I${TACC_CUDA_INC} -lnvidia-ml -c $<

data_collect_eth.o data_collect_ib.o data_collect_opa.o: remora_network.h

%.o: %.c
	@$(CC) $(CCFLAGS) $(DEPFLAGS) -c $<

clean:
	@rm -rf $(OBJECTS)

clean-all:
	rm -rf $(OBJECTS) $(BINARIES)

.PHONY : all  clean clean-all

# Optional network-resource snapshots.
OBJECTS += data_collect_netinfo.o data_collect_neterrors.o data_collect_ibcounters.o data_collect_mlxcounters.o data_collect_opacounters.o
BINARIES += data_collect_netinfo data_collect_neterrors data_collect_ibcounters data_collect_mlxcounters data_collect_opacounters
all: data_collect_netinfo data_collect_neterrors data_collect_ibcounters data_collect_mlxcounters data_collect_opacounters

data_collect_netinfo: data_collect_netinfo.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) $< -o $(BUILD_DIR)/$@

data_collect_netinfo.o: remora_netresources.h

data_collect_neterrors: data_collect_neterrors.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) $< -o $(BUILD_DIR)/$@

data_collect_neterrors.o: remora_netresources.h

data_collect_ibcounters: data_collect_ibcounters.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) $< -o $(BUILD_DIR)/$@

data_collect_ibcounters.o: remora_netresources.h

data_collect_mlxcounters: data_collect_mlxcounters.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) $< -o $(BUILD_DIR)/$@

data_collect_mlxcounters.o: remora_netresources.h

data_collect_opacounters: data_collect_opacounters.o
	$(shell mkdir -p $(BUILD_DIR))
	@$(CC) $< -o $(BUILD_DIR)/$@

data_collect_opacounters.o: remora_netresources.h

# Optional proc network-resource snapshots.
OBJECTS += data_collect_netprotocol.o data_collect_softnet.o
BINARIES += data_collect_netprotocol data_collect_softnet
all: data_collect_netprotocol data_collect_softnet

data_collect_netprotocol: data_collect_netprotocol.o
	@mkdir -p $(BUILD_DIR)
	$(CC) $< -o $(BUILD_DIR)/$@

data_collect_softnet: data_collect_softnet.o
	@mkdir -p $(BUILD_DIR)
	$(CC) $< -o $(BUILD_DIR)/$@

data_collect_netprotocol.o data_collect_softnet.o: remora_procnet.h

# Read-only driver statistics modules (default-off in installer).
OBJECTS += data_collect_nicstats.o data_collect_linkhealth.o
BINARIES += data_collect_nicstats data_collect_linkhealth
all: data_collect_nicstats data_collect_linkhealth

data_collect_nicstats: data_collect_nicstats.o
	@mkdir -p $(BUILD_DIR)
	$(CC) $< -o $(BUILD_DIR)/$@

data_collect_linkhealth: data_collect_linkhealth.o
	@mkdir -p $(BUILD_DIR)
	$(CC) $< -o $(BUILD_DIR)/$@

data_collect_nicstats.o data_collect_linkhealth.o: remora_nicstats.h remora_nicfields.h

# Read-only DCB configuration snapshot, no privileged setters.
OBJECTS += data_collect_netdcb.o
BINARIES += data_collect_netdcb
all: data_collect_netdcb
data_collect_netdcb: data_collect_netdcb.o
	@mkdir -p $(BUILD_DIR)
	$(CC) $< -o $(BUILD_DIR)/$@
data_collect_netdcb.o: remora_netdcb.h
