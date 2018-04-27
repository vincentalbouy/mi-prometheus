import torch
import torch.nn.functional as F
import numpy as np

from models.ntm.tensor_utils import circular_conv, normalize
from models.ntm.memory import Memory


class Interface:
    def __init__(self, num_heads, is_cam, num_shift, M):
        """Initialize Interface.

        :param num_heads: number of heads
        :param is_cam [boolean]: are the heads allowed to use content addressing
        :param num_shift: number of shifts of heads.
        :param M: Number of slots per address in the memory bank.
        """
        self.num_heads = num_heads
        self.M = M

        # Define a dictionary for attentional parameters
        self.is_cam = is_cam
        self.param_dict = {'s': num_shift, 'jd': num_heads, 'j': 3*num_heads, 'γ': 1, 'erase': M, 'add': M}
        if self.is_cam:
            self.param_dict.update({'k': M, 'β': 1, 'g': 1})

        # create the parameter lengths and store their cumulative sum
        lengths = np.fromiter(self.param_dict.values(), dtype=int)
        self.cum_lengths = np.cumsum(np.insert(lengths, 0, 0), dtype=int).tolist()

    @property
    def read_size(self):
        """
        Returns the size of the data read by all heads
        
        :return: (num_head*content_size)
        """
        return self.num_heads * self.M

    @property
    def update_size(self):
        """
        Returns the total number of parameters output by the controller
        
        :return: (num_heads*parameters_per_head)
        """
        return self.num_heads * self.cum_lengths[-1]

    def read(self, wt, mem):
        """Returns the data read from memory

        :param wt: the read weight [BATCH_SIZE, MEMORY_SIZE]
        :param mem: the memory [BATCH_SIZE, CONTENT_SIZE, MEMORY_SIZE] 
        :return: the read data [BATCH_SIZE, CONTENT_SIZE]
        """
        memory = Memory(mem)
        read_data = memory.attention_read(wt)
        # flatten the data_gen in the last 2 dimensions
        sz = read_data.size()[:-2]
        return read_data.view(*sz, self.read_size)

    def update(self, update_data, wt, wt_dynamic, mem):
        """Erases from memory, writes to memory, updates the weights using various attention mechanisms
        :param update_data: the parameters from the controllers [update_size]
        :param wt: the read weight [BATCH_SIZE, MEMORY_SIZE]
        :param mem: the memory [BATCH_SIZE, CONTENT_SIZE, MEMORY_SIZE] 
        :return: TUPLE [wt, mem]
        """
        assert update_data.size()[-1] == self.update_size, "Mismatch in update sizes"

        # reshape update data_gen by heads and total parameter size
        sz = update_data.size()[:-1]
        update_data = update_data.view(*sz, self.num_heads, self.cum_lengths[-1])

        # split the data_gen according to the different parameters
        data_splits = [update_data[..., self.cum_lengths[i]:self.cum_lengths[i+1]]
                       for i in range(len(self.cum_lengths)-1)]

        # Obtain update parameters
        if self.is_cam:
            s, jd, j, γ, erase, add, k, β, g = data_splits
            # Apply Activations
            k = F.tanh(k)                  # key vector used for content-based addressing
            β = F.softplus(β)              # key strength used for content-based addressing
            g = F.sigmoid(g)               # interpolation gate
        else:
            s, jd, j, γ, erase, add = data_splits

        s = F.softmax(F.softplus(s), dim=-1)    # shift weighting (determines how the weight is rotated)
        γ = 1 + F.softplus(γ)                   # used for weight sharpening
        erase = F.sigmoid(erase)                # erase memory content

        # Write to memory
        memory = Memory(mem)
        memory.erase_weighted(erase, wt)
        memory.add_weighted(add, wt)

        ## set jumping mechanisms
        #  fixed attention to address 0
        wt_address_0 = torch.zeros_like(wt)
        wt_address_0[:, 0:self.num_heads, 0] = 1

        # interpolation between wt and wt_d
        jd = F.sigmoid(jd)
        wt_dynamic = (1 - jd) * wt + jd * wt_dynamic

        # interpolation between wt_0 wt_d wt
        j = F.softmax(j, dim=-1)
        j = j.view(-1, self.num_heads, 3)
        j = j[:, :, None, :]

        wt = j[..., 0] * wt_dynamic \
           + j[..., 1] * wt \
           + j[..., 2] * wt_address_0

        # Update attention
        if self.is_cam:
            wt_k = memory.content_similarity(k)               # content addressing ...
            wt_β = F.softmax(β * wt_k, dim=-1)                # ... modulated by β
            wt = g * wt_β + (1 - g) * wt                      # scalar interpolation

        wt_s = circular_conv(wt, s)                   # convolution with shift

        eps = 1e-12
        wt = (wt_s + eps) ** γ
        wt = normalize(wt)                    # sharpening with normalization

        if torch.sum(torch.abs(torch.sum(wt[:,0,:], dim=-1) - 1.0)) > 1e-6:
            print("error: gamma very high, normalization problem")

        mem = memory.content
        return wt, wt_dynamic, mem
