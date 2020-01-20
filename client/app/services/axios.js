import axiosLib from "axios";
import qs from "query-string";

export const axios = axiosLib.create({
  paramsSerializer: params => qs.stringify(params),
});

const getData = ({ data }) => data;
const getResponse = ({ response }) => Promise.reject(response);

axios.interceptors.response.use(getData, getResponse);

// TODO: revisit this definition when auth is updated
export default function init(ngModule) {
  ngModule.run($injector => {
    axios.interceptors.request.use(config => {
      const apiKey = $injector.get("Auth").getApiKey();
      if (apiKey) {
        config.headers.Authorization = `Key ${apiKey}`;
      }

      return config;
    });
  });
}

init.init = true;
