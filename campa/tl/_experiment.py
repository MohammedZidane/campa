from copy import deepcopy
from typing import Any, Dict, Iterable
import os
import re
import glob
import json
import logging

import pandas as pd
import tensorflow as tf

from campa.tl import LossEnum, ModelEnum
from campa.data import MPPData
from campa.utils import load_config, merged_config
from campa.constants import EXPERIMENT_DIR, get_data_config


class Experiment:
    # base experiment config
    config: Dict[str, Any] = {
        "experiment": {
            "dir": None,
            "name": "experiment",
            "save_config": True,
        },
        "data": {
            "data_config": None,
            "dataset_name": None,
            "output_channels": None,
        },
        "model": {
            "model_cls": ModelEnum.BaseAEModel,  # instance or value of ModelEnum
            "model_kwargs": {},
            # if true, looks for saved weights in experiment_dir
            # if a path, loads these weights
            "init_with_weights": False,
        },
        "training": {
            "learning_rate": 0.001,
            "epochs": 10,
            "batch_size": 128,
            "loss": {"decoder": LossEnum.MSE},  # instance or value of LossEnum
            "loss_weights": {"decoder": 1},
            "loss_warmup_to_epoch": {},
            "metrics": {"decoder": LossEnum.MSE},  # instance or value of LossEnum
            # saving models
            "save_model_weights": True,
            "save_history": True,
            "overwrite_history": True,
        },
        "evaluation": {  # TODO change this to fit to aggregation params
            "split": "val",
            "predict_reps": ["latent", "decoder"],
            "img_ids": 25,
            "predict_imgs": True,
        },
        "cluster": {  # cluster config, also used in this format for whole data clustering
            "predict_cluster_imgs": True,
            "cluster_name": "clustering",
            "cluster_rep": "latent",
            "cluster_method": "leiden",  # leiden or kmeans
            "leiden_resolution": 0.8,
            "subsample": None,  # 'subsample' or 'som'
            "subsample_kwargs": {},
            "som_kwargs": {},
            "umap": True,
        },
    }

    def __init__(self, config):
        self.config = merged_config(self.config, config)
        self.log = logging.getLogger(self.__class__.__name__)
        self.log.info(f"Setting up experiment {self.dir}/{self.name}")
        data_config = get_data_config(self.config["data"]["data_config"])
        # load data_params
        self.data_params = json.load(
            open(
                os.path.join(
                    data_config.DATASET_DIR,
                    self.config["data"]["dataset_name"],
                    "params.json",
                ),
            )
        )
        # create exp_path
        if self.dir is not None:
            os.makedirs(self.full_path, exist_ok=True)
            if self.config["experiment"]["save_config"]:
                self.log.info(f"Saving config to {self.dir}/{self.name}/config.json")
                json.dump(
                    self.config,
                    open(os.path.join(self.full_path, "config.json"), "w"),
                    indent=4,
                )
        else:
            self.log.info("exp_dir is None, did not save config")

    @classmethod
    def from_dir(cls, exp_path: str):
        """
        init experiment from trained experiment in exp_path.
        Changes init_with_weights to True and save_config to False

        Parameters
        ----------
        exp_path
            path to experiment, relative to EXPERIMENT_DIR
        """
        # load config from json
        config_fname = os.path.join(EXPERIMENT_DIR, exp_path, "config.json")
        assert os.path.exists(config_fname), f"no config.json in {exp_path}"
        config = json.load(open(config_fname))
        # set save_config to False to avoid overwriting
        config["experiment"]["save_config"] = False
        self = cls(config)
        self.log.info(f"Initialised from existing experiment in {self.dir}/{self.name}")
        return self

    def set_to_evaluate(self):
        # changes init_with_weights to True to load correct weights in Estimator
        self.config["model"]["init_with_weights"] = True
        return self

    @property
    def is_trainable(self):
        return self.config["model"] is not None and self.config["training"] is not None

    @property
    def name(self):
        return self.config["experiment"]["name"]

    @property
    def dir(self):  # noqa: A003
        return self.config["experiment"]["dir"]

    @property
    def full_path(self):
        return os.path.join(EXPERIMENT_DIR, self.dir, self.name)

    @property
    def estimator_config(self):
        estimator_config = {
            key: val for key, val in self.config.items() if key in ["experiment", "data", "model", "training"]
        }
        # return copy to avoid side effects on self.config
        return deepcopy(estimator_config)

    @property
    def evaluate_config(self):
        evaluate_config = self.config["evaluation"]
        return deepcopy(evaluate_config)

    def get_history(self):
        history_path = os.path.join(self.full_path, "history.csv")
        if os.path.isfile(history_path):
            return pd.read_csv(history_path, index_col=0)
        else:
            return None

    @property
    def epoch(self):
        """
        last epoch for which there is a trained model
        """
        weights_path = tf.train.latest_checkpoint(self.full_path)
        if weights_path is None:
            return 0
        # find epoch in weights_path
        res = re.findall(r"epoch(\d\d\d)", os.path.basename(weights_path))
        if len(res) == 0:
            return 0
        else:
            return int(res[0])

    def get_split_mpp_data(self):
        """
        val / test from results_epochXXX
        """
        split = self.config["evaluation"]["split"]
        data_dir = os.path.join(self.full_path, f"results_epoch{self.epoch:03d}", split)
        if os.path.isdir(data_dir):
            return MPPData.from_data_dir(
                data_dir,
                base_dir="",
                keys=["x", "y", "obj_ids", "mpp"],
                optional_keys=list(
                    {
                        self.config["cluster"]["cluster_rep"],
                        "latent",
                        "decoder",
                        self.config["cluster"]["cluster_name"],
                        "umap",
                    }
                ),
                data_config=self.config["data"]["data_config"],
            )
        return None

    def get_split_imgs_mpp_data(self):
        """
        val_imgs / test_imgs from results_epochXXX
        """
        split = self.config["evaluation"]["split"]
        data_dir = os.path.join(self.full_path, f"results_epoch{self.epoch:03d}", split + "_imgs")
        if os.path.isdir(data_dir):
            return MPPData.from_data_dir(
                data_dir,
                base_dir="",
                keys=["x", "y", "obj_ids", "mpp"],
                optional_keys=list(
                    {
                        self.config["cluster"]["cluster_rep"],
                        "latent",
                        "decoder",
                        self.config["cluster"]["cluster_name"],
                        "umap",
                    }
                ),
                data_config=self.config["data"]["data_config"],
            )
        return None

    def get_sub_mpp_data(self):
        """
        subsampled mpp data from aggregated/sub
        """

    def get_split_cluster_annotation(self, cluster_name="clustering"):
        """
        Reads cluster_annotation file from disk for evaluation split
        """
        fname = os.path.join(
            self.full_path,
            f"results_epoch{self.epoch:03d}",
            self.config["evaluation"]["split"],
            f"{cluster_name}_annotation.csv",
        )
        # TODO this reading is duplicated in Cluster (where annotation is first created)
        return pd.read_csv(fname, index_col=0, dtype=str, keep_default_na=False)

    def get_cluster_annotation(self, cluster_name="clustering", cluster_dir=None):
        """
        Read cluster_annotation for full data from disk
        If cluster_dir is none, is inferred from filesystem
        """
        # TODO need to somehow figure out sub dir!
        if cluster_dir is None:
            for f in glob.glob(os.path.join(self.full_path, "aggregated/sub-*")):
                cluster_dir = "aggregated/" + os.path.basename(f)
                self.log.info(f"Cluster annotation: using cluster data in {cluster_dir}")
                break
        fname = os.path.join(self.full_path, cluster_dir, f"{cluster_name}_annotation.csv")
        return pd.read_csv(fname, index_col=0, dtype=str, keep_default_na=False)

    @staticmethod
    def get_experiments_from_config(config_fname, exp_names=None):
        """
        init and return experiments from configs in config.py file
        """
        config = load_config(config_fname)
        exps = []
        for exp_config in config.variable_config:
            cur_config = merged_config(config.base_config, exp_config)
            if exp_names is None or cur_config["experiment"]["name"] in exp_names:
                exps.append(Experiment(cur_config))
        return exps

    @staticmethod
    def get_experiments_from_dir(exp_dir, exp_names=None, only_trainable=False):
        exps = []
        for exp_name in next(os.walk(os.path.join(EXPERIMENT_DIR, exp_dir)))[1]:
            config_fname = os.path.join(EXPERIMENT_DIR, exp_dir, exp_name, "config.json")
            if os.path.exists(config_fname) and ((exp_names is None) or (exp_name in exp_names)):
                exp = Experiment.from_dir(os.path.join(exp_dir, exp_name))
                if not only_trainable or exp.is_trainable:
                    exps.append(exp)
        return exps


def run_experiments(exps: Iterable[Experiment], mode: str = "all"):
    """
    Execute experiments

    Runs all given experiments in the given mode.
    The following modes are available:
    - "train": train experiments (if trainable)
    - "evaluate": predict experiments on val set and cluster results (on val set)
    - "trainval": both train and evaluate
    - "compare": generate comparative plots of experiments
    - "all": trainval and compare

    Parameters
    ----------
    exps
        experiments to run
    mode
        mode, one of "train", "evaluate", "trainval", "compare", "all"
    """
    from campa.tl import Cluster, Estimator, Predictor, ModelComparator

    assert mode in ["train", "evaluate", "trainval", "compare", "all"], f"unknown mode {mode}"
    exp_names = [exp.name for exp in exps]
    print(f"Running experiment for {exp_names} with mode {mode}")
    for exp_name, exp in zip(exp_names, exps):
        if mode in ("all", "train", "trainval"):
            if exp.is_trainable:
                print(f"Training model for {exp_name}")
                est = Estimator(exp)
                _ = est.train_model()
        if mode in ("all", "evaluate", "trainval"):
            if exp.is_trainable:
                # evaluate model
                print(f"Evaluating model for {exp_name}")
                pred = Predictor(exp)
                pred.evaluate_model()
            else:
                _prepare_exp_split(exp)
            # cluster model
            print(f"Clustering results for {exp_name}")
            cl = Cluster.from_exp_split(exp)
            cl.create_clustering()
            # predict cluster for images
            if exp.config["evaluation"]["predict_cluster_imgs"]:
                cl.predict_cluster_imgs(exp)
    # compare models
    if mode in ("all", "compare"):
        # assumes that all experiments have the same experiment_dir
        comp = ModelComparator(exps, save_dir=os.path.join(EXPERIMENT_DIR, exps[0].dir))
        comp.plot_history(values=["val_loss", "val_decoder_loss"])
        comp.plot_final_score(
            score="val_decoder_loss",
            fallback_score="val_loss",
            save_prefix="decoder_loss_",
        )
        comp.plot_per_channel_mse()
        comp.plot_predicted_images(img_ids=[0, 1, 2, 3, 4], img_size=exps[0].data_params["test_img_size"])
        comp.plot_cluster_images(img_ids=[0, 1, 2, 3, 4], img_size=exps[0].data_params["test_img_size"])
        comp.plot_umap()


def _prepare_exp_split(exp):
    """
    set up exp split data for non trainable model. Mimicks results folders created with predictor
    """
    import numpy as np

    from campa.data import MPPData

    # create results mpp_data for not trainable experiment to allow usage with Cluster
    for split in [
        exp.config["evaluation"]["split"],
        exp.config["evaluation"]["split"] + "_imgs",
    ]:
        base_data_dir = os.path.join("datasets", exp.data_params["dataset_name"], split)
        mpp_params = {"base_data_dir": base_data_dir, "subset": True}
        mpp_data = MPPData.from_data_dir(base_data_dir, data_config=exp.config["data"]["data_config"])
        if "_imgs" in split:
            # choose random img_ids from availalbe ones
            rng = np.random.default_rng(seed=42)
            img_ids = rng.choice(
                mpp_data.unique_obj_ids,
                exp.config["evaluation"]["img_ids"],
                replace=False,
            )
            # subset mpp_data to these img_ids
            mpp_data.subset(obj_ids=img_ids)
        mpp_data.write(
            save_dir=os.path.join(exp.full_path, "results_epoch000", split),
            mpp_params=mpp_params,
            save_keys=[],
        )
