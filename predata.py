import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
import numpy as np #type:ignore
from rdkit import Chem #type:ignore
from rdkit.Chem.rdchem import BondType #type:ignore
from graph_features import atom_features
from collections import defaultdict
from transformers import AutoModel, AutoTokenizer #type:ignore
import torch #type:ignore
import pickle
import csv

BONDTYPE_TO_INT = defaultdict(
    lambda: 0,
    {
        BondType.SINGLE: 0,
        BondType.DOUBLE: 1,
        BondType.TRIPLE: 2,
        BondType.AROMATIC: 3
    }
)


protein_dict = { "A": 1, "C": 2, "B": 3, "E": 4, "D": 5, "G": 6,
                 "F": 7, "I": 8, "H": 9, "K": 10, "M": 11, "L": 12,
                 "O": 13, "N": 14, "Q": 15, "P": 16, "S": 17, "R": 18,
                 "U": 19, "T": 20, "W": 21,
                 "V": 22, "Y": 23, "X": 24,
                 "Z": 25 }

CHAR_SMI_SET = {"(": 1, ".": 2, "0": 3, "2": 4, "4": 5, "6": 6, "8": 7, "@": 8,
                "B": 9, "D": 10, "F": 11, "H": 12, "L": 13, "N": 14, "P": 15, "R": 16,
                "T": 17, "V": 18, "Z": 19, "\\": 20, "b": 21, "d": 22, "f": 23, "h": 24,
                "l": 25, "n": 26, "r": 27, "t": 28, "#": 29, "%": 30, ")": 31, "+": 32,
                "-": 33, "/": 34, "1": 35, "3": 36, "5": 37, "7": 38, "9": 39, "=": 40,
                "A": 41, "C": 42, "E": 43, "G": 44, "I": 45, "K": 46, "M": 47, "O": 48,
                "S": 49, "U": 50, "W": 51, "Y": 52, "[": 53, "]": 54, "a": 55, "c": 56,
                "e": 57, "g": 58, "i": 59, "m": 60, "o": 61, "s": 62, "u": 63, "y": 64}

def smile_to_graph(smile):
    molecule = Chem.MolFromSmiles(smile)
    n_atoms = molecule.GetNumAtoms()
    atoms = [molecule.GetAtomWithIdx(i) for i in range(n_atoms)]

    adjacency = Chem.rdmolops.GetAdjacencyMatrix(molecule)
    node_features = np.array([atom_features(atom) for atom in atoms])

    n_edge_features = 4
    edge_features = np.zeros([n_atoms, n_atoms, n_edge_features])
    for bond in molecule.GetBonds():
        i = bond.GetBeginAtomIdx()
        j = bond.GetEndAtomIdx()
        bond_type = BONDTYPE_TO_INT[bond.GetBondType()]
        edge_features[i, j, bond_type] = 1
        edge_features[j, i, bond_type] = 1

    return node_features, adjacency

def first_sequence(sequence):
    words = [protein_dict[sequence[i]]
             for i in range(len(sequence))]
    return np.array(words)

if torch.cuda.is_available():
    device = torch.device('cuda')
else:
    device = torch.device('cpu')

text_tokenizer = AutoTokenizer.from_pretrained("dmis-lab/biobert-base-cased-v1.1", do_lower_case=False)
text_model = AutoModel.from_pretrained("dmis-lab/biobert-base-cased-v1.1").to(device)

chem_tokenizer = AutoTokenizer.from_pretrained("seyonec/PubChem10M_SMILES_BPE_450k", do_lower_case=False)
chem_model = AutoModel.from_pretrained("seyonec/PubChem10M_SMILES_BPE_450k").to(device)


def Preprocess(dataset, dir_input):
    with open(dataset, newline='', encoding='utf-8') as csvfile:
        csv_reader = csv.reader(csvfile)
        data_list = []
        for row in csv_reader:
            data_list.append(row)
    N = len(data_list)
    molecule_atoms, molecule_adjs, molecule_words, proteins, sequencess, smiless, interactions = [], [], [], [], [], [], []
    p_LM, d_LM = {}, {}
    for no, data in enumerate(data_list):
        print('/'.join(map(str, [no + 1, N])))
        smiles, sequences, text, interaction = data
        if len(sequences) > 5000:
            sequences = sequences[0:5000]
        if len(smiles) > 512:
            continue
        sequencess.append(sequences)
        smiless.append(smiles)
        text_input = text_tokenizer.batch_encode_plus([text], add_special_tokens=True, padding=True, truncation=True, max_length=512)
        t_IDS = torch.tensor(text_input["input_ids"]).to(device)
        t_a_m = torch.tensor(text_input["attention_mask"]).to(device)
        with torch.no_grad():
            text_outputs = text_model(input_ids=t_IDS, attention_mask=t_a_m)
        text_feature = text_outputs.last_hidden_state.squeeze(0).to('cpu').data.numpy()
        if sequences not in p_LM:
            p_LM[sequences] = text_feature

        chem_input = chem_tokenizer.batch_encode_plus([smiles], add_special_tokens=True, padding=True)
        c_IDS = torch.tensor(chem_input["input_ids"]).to(device)
        c_a_m = torch.tensor(chem_input["attention_mask"]).to(device)
        with torch.no_grad():
            chem_outputs = chem_model(input_ids=c_IDS, attention_mask=c_a_m)
        chem_feature = chem_outputs.last_hidden_state.squeeze(0).to('cpu').data.numpy()
        if smiles not in d_LM:
            d_LM[smiles] = chem_feature

        molecule_word = []
        for i in range(len(smiles)):
            molecule_word.append(CHAR_SMI_SET[smiles[i]])
        molecule_word = np.array(molecule_word)
        molecule_words.append(molecule_word)

        atom_feature, adj = smile_to_graph(smiles)
        molecule_atoms.append(atom_feature)
        molecule_adjs.append(adj)

        protein_first = first_sequence(sequences)
        proteins.append(protein_first)

        interactions.append(np.array([float(interaction)]))

    with open(dir_input + "p_LM.pkl", "wb") as p:
        pickle.dump(p_LM, p)

    with open(dir_input + "d_LM.pkl", "wb") as d:
        pickle.dump(d_LM, d)

    np.save(dir_input + 'molecule_words', molecule_words)
    np.save(dir_input + 'molecule_atoms', molecule_atoms)
    np.save(dir_input + 'molecule_adjs', molecule_adjs)
    np.save(dir_input + 'proteins', proteins)
    np.save(dir_input + 'sequences', sequencess)
    np.save(dir_input + 'smiles', smiless)
    np.save(dir_input + 'interactions', interactions)


if __name__ == "__main__":

    Preprocess(".//datasets//DHC//original//data_text.csv", './/datasets//DHC//train//')
